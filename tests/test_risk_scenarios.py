import json
import unittest
from pathlib import Path

from src.daily_planning.risk.scenarios import generate_scenarios, load_scenario_config
from src.daily_planning.risk.service import evaluate_monte_carlo, evaluate_with_adapter
from src.daily_planning.risk.replay import evaluate_scenario


ROOT = Path(__file__).resolve().parents[1]


class ScenarioTest(unittest.TestCase):
    def setUp(self):
        self.config = load_scenario_config(ROOT / 'config/risk_synthetic_v1.json')
        self.config['sample_count'] = 11

    def test_seed_and_call_order_reproducible(self):
        a = generate_scenarios(self.config, ['DEM-2', 'DEM-1'])
        b = generate_scenarios(self.config, ['DEM-1', 'DEM-2'])
        self.assertEqual(a,b)
        self.assertEqual(len(a),11)
        self.assertEqual(a[0]['provenance'],'SYNTHETIC_ASSUMPTION')
        self.assertEqual(len({s['scenario_id'] for s in a}),11)

    def test_seed_or_config_change_changes_scenarios(self):
        a = generate_scenarios(self.config,['DEM-1'])
        self.config['seed'] = 43
        b = generate_scenarios(self.config,['DEM-1'])
        self.assertNotEqual(a,b)

    def test_multiple_weather_blocks_are_seeded_and_backward_compatible(self):
        legacy = generate_scenarios(self.config, ['DEM-1'])
        self.assertNotIn('weather_blocks', legacy[0])
        self.config['weather_blocks'] = [
            {'duration_minutes': {'60': 1}, 'onset_offset_minutes': {'120': 1}},
            {'duration_minutes': {'30': 1}, 'onset_offset_minutes': {'150': 1}},
        ]
        first = generate_scenarios(self.config, ['DEM-1', 'DEM-2'])
        second = generate_scenarios(self.config, ['DEM-2', 'DEM-1'])
        self.assertEqual(first, second)
        self.assertEqual(first[0]['weather_blocks'], [
            {'duration_minutes': 60, 'onset_offset_minutes': 120},
            {'duration_minutes': 30, 'onset_offset_minutes': 150}])
        self.config['weather_blocks'][0]['duration_minutes'] = {'-1': 1}
        with self.assertRaises(ValueError):
            generate_scenarios(self.config, ['DEM-1'])

    def test_bad_config_and_calls(self):
        self.config['arrival_shift_minutes'] = {'0':0.8,'60':0.3}
        with self.assertRaises(ValueError):
            generate_scenarios(self.config,['DEM-1'])
        self.config = load_scenario_config(ROOT / 'config/risk_synthetic_v1.json')
        with self.assertRaises(ValueError):
            generate_scenarios(self.config,['DEM-1','DEM-1'])

    def test_evaluation_requires_actual_callback(self):
        with self.assertRaises(ValueError):
            evaluate_scenario({}, {}, {'scenario_id':'s1'})

    def test_synthetic_replay_callback_only_for_unit_test(self):
        # This validates orchestration and CVaR with a *dummy unit-test callback*.
        # The demo callback is NOT a terminal demurrage calculator.
        configs = [{'scenario_id':'s1','scenario_weight':'0.6'},
                   {'scenario_id':'s2','scenario_weight':'0.4'}]
        def stub(candidate, scenario):
            return {'A':{'s1':100,'s2':200},'B':{'s1':90,'s2':220}}[candidate['label']][scenario['scenario_id']]
        report = evaluate_with_adapter([{'label':'A'},{'label':'B'}],configs,stub)
        self.assertEqual(report['risk_by_candidate']['A']['expected_cost_cents'],'140.000000')
        self.assertEqual(report['risk_by_candidate']['B']['probability_cheaper_than_baseline'],'0.600000')
        self.assertEqual(report['provenance'],'SYNTHETIC_ASSUMPTION')

    def test_failure_rates_are_separate_from_unpriced_cost_risk(self):
        scenarios = [{'scenario_id':'s1','scenario_weight':'0.5'},
                     {'scenario_id':'s2','scenario_weight':'0.5'}]
        def callback(candidate, scenario):
            if candidate['label'] == 'B' and scenario['scenario_id'] == 's2':
                return dict(status='FAILED', failure_reason='FROZEN_ASSIGNMENT_CONFLICT',
                            cost_cents=None)
            return dict(status='SUCCEEDED', cost_cents=100)
        report = evaluate_with_adapter([{'label':'A'}, {'label':'B'}], scenarios, callback)
        self.assertIsNone(report['risk_by_candidate'])
        self.assertEqual(report['failure_rates_by_candidate']['B']['failure_probability'],
                         '0.500000')
        self.assertEqual(report['failure_rates_by_candidate']['A']['failed_count'], 0)
        self.assertEqual(report['failures'][0]['scenario_id'], 's2')
        self.assertFalse(report['full_distribution_available'])
        self.assertIsNone(report['risk_by_candidate'])
        self.assertEqual(report['coverage_by_candidate']['B'], dict(
            physical_success=1, settlement_success=1, unresolved=0,
            infeasible=1, missing=0, scenario_count=2))
        self.assertEqual(report['conditional_risk_by_candidate']['B']['sample_count'], 1)

    def test_unresolved_and_missing_outcomes_never_enter_full_cost_risk(self):
        scenarios = [{'scenario_id': f's{x}', 'scenario_weight': '0.333333333333333'}
                     for x in range(3)]
        def callback(candidate, scenario):
            sid = scenario['scenario_id']
            if candidate['label'] == 'B' and sid == 's1':
                return dict(status='FAILED', cost_cents=None,
                            failure_class='UNRESOLVED', physical_success=True,
                            settlement_success=False,
                            settlement_rule_status='UNRESOLVED_SUBMINUTE_SETTLEMENT_RULE')
            if candidate['label'] == 'C' and sid == 's2':
                return None
            return dict(status='SUCCEEDED', cost_cents=100)
        report = evaluate_with_adapter([{'label': x} for x in 'ABC'], scenarios, callback)
        self.assertFalse(report['full_distribution_available'])
        self.assertEqual(report['coverage_by_candidate']['B']['physical_success'], 3)
        self.assertEqual(report['coverage_by_candidate']['B']['unresolved'], 1)
        self.assertEqual(report['coverage_by_candidate']['C']['missing'], 1)
        self.assertEqual(report['conditional_paired_risk']['scenario_count'], 1)

    def test_real_seeded_replay_reports_vessel_losses_and_paired_metrics(self):
        from .test_risk_arrival_replay import fixture
        snap, first = fixture()
        candidates = [dict(first, label=label, candidate_id=label) for label in 'ABC']
        config = dict(self.config, sample_count=5)
        report = evaluate_monte_carlo(snap, candidates, config)
        self.assertEqual(report['scenario_count'], 5)
        self.assertEqual(report['risk_status'], 'SUCCEEDED')
        self.assertEqual(report['risk_by_candidate']['A']['expected_savings_vs_baseline_cents'],
                         '0.000000')
        self.assertEqual(set(report['vessel_loss_by_candidate']['B']),
                         {row['call_id'] for row in first['vessel_results']})
        self.assertEqual(report['scenario_results']['A'].keys(),
                         report['scenario_results']['C'].keys())


if __name__ == '__main__':
    unittest.main()
