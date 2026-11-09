"""Explicit policy activation, Golden gate and paired scenario coverage."""
import unittest
from pathlib import Path

from src.daily_planning.risk.golden_validation import load_settlement_config
from src.daily_planning.risk.scenarios import load_scenario_config, generate_scenarios
from src.daily_planning.risk.service import evaluate_monte_carlo, evaluate_with_adapter
from src.daily_planning.risk.second_precision import POLICY_VERSION
from src.daily_planning.risk.replay import evaluate_scenario
from .test_risk_arrival_replay import fixture

ROOT=Path(__file__).resolve().parents[1]


class RiskPolicyConfigV3Test(unittest.TestCase):
    def setUp(self):
        self.settlement=load_settlement_config(ROOT/'config/risk_settlement_second_v1.json')
        self.config=load_scenario_config(ROOT/'config/risk_synthetic_v1.json')
        self.config.update(sample_count=5,
            arrival_shift_minutes={'0':1},
            work_duration_multiplier={'1.005':1},
            additional_weather_block_minutes={'0':1},
            weather_block_onset_offset_minutes={'0':1})
        self.snapshot,self.candidate=fixture()
        self.candidates=[dict(self.candidate,label=label,candidate_id=label) for label in 'ABC']

    def test_second_precision_requires_json_and_reports_clock_conflict(self):
        with self.assertRaisesRegex(ValueError,'requires an explicit versioned JSON'):
            evaluate_monte_carlo(self.snapshot,self.candidates,self.config,
                settlement_policy=POLICY_VERSION)
        with self.assertRaisesRegex(ValueError,'effective-start'):
            evaluate_monte_carlo(self.snapshot,self.candidates,self.config,
                settlement_policy=POLICY_VERSION,
                settlement_config=self.settlement)

    def test_no_bypass_and_isolated_abc_scenarios_remain_paired(self):
        config=dict(self.settlement,clock_start_divergence_policy='REPORT_AND_ALLOW')
        from src.daily_planning.risk.golden_validation import load_settlement_config
        import json,tempfile
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError,'must remain BLOCK'):
                load_settlement_config(path)
        scenarios=generate_scenarios(self.config,
            [r['call_id'] for r in self.candidate['vessel_results']])
        report=evaluate_with_adapter(self.candidates,scenarios,
            lambda candidate,scenario: evaluate_scenario(self.snapshot,candidate,
                scenario,settlement_policy=POLICY_VERSION))
        self.assertEqual(report['scenario_count'],5)
        self.assertTrue(report['full_distribution_available'])
        self.assertEqual(report['coverage_by_candidate']['A']['settlement_success'],5)
        self.assertEqual(report['scenario_results']['A'].keys(),
                         report['scenario_results']['B'].keys())
        self.assertEqual(report['scenario_results']['B'].keys(),
                         report['scenario_results']['C'].keys())
        self.assertEqual(report['risk_by_candidate']['A']['expected_savings_vs_baseline_cents'],
                         '0.000000')


if __name__=='__main__':
    unittest.main()
