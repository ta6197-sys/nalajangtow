"""V2 freeze semantics and causal replay acceptance checks."""
import copy
import unittest
from datetime import timedelta

from weather_optimizer import WeatherSeries

from src.daily_planning.context import engine_calls, local_time, signature
from src.daily_planning.evaluation import evaluate_baseline, price_until, replay_row
from src.daily_planning.risk.replay import evaluate_scenario
from src.daily_planning.risk.scenarios import generate_scenarios, load_scenario_config
from src.daily_planning.risk.service import evaluate_with_adapter
from src.daily_planning.risk.time_precision import berth_overlap
from .test_risk_arrival_replay import fixture
from .test_risk_replay import WEATHER_PATH, snapshot


def ongoing_fixture():
    snap = snapshot()
    snap['as_of_at'] = '2026-11-02T02:00:00+09:00'
    snap['calls'][0]['available_at'] = '2026-11-02T01:00:00+09:00'
    weather = WeatherSeries.from_csv(WEATHER_PATH)
    call = engine_calls(snap)[0]
    original = replay_row(call, 'BERTH-01', call['available_at'], weather)
    state = snap['call_states'][0]
    state.update(status='IN_PROGRESS', berth_id='BERTH-01', remaining_work_kg=60000,
                 accrued_demurrage_cents=price_until(call, original,
                     local_time(snap['as_of_at']), weather)['demurrage_cents'],
                 contract_clock_state={'remaining_work_minutes': 60})
    snap['commits'] = [dict(call_id='RISK-001', berth_id='BERTH-01',
        start_at='2026-11-02T01:00:00+09:00',
        completion_at='2026-11-02T03:00:00+09:00',
        locked_from_at='2026-11-02T01:00:00+09:00',
        locked_until_at='2026-11-02T03:00:00+09:00')]
    snap['snapshot_hash'] = signature({k: v for k,v in snap.items() if k != 'snapshot_hash'})
    evaluation = evaluate_baseline(snap, [], weather)['evaluation']
    candidate = dict(candidate_id='A', input_snapshot_hash=snap['snapshot_hash'],
        cost_cents=evaluation['summary']['demurrage_increment_to_completion_cents'],
        wait_minutes=evaluation['summary']['total_wait_minutes'],
        vessel_results=evaluation['vessel_results'])
    return snap, candidate


class ShockAwareFreezeTest(unittest.TestCase):
    def test_started_work_preserves_history_and_shocks_only_remaining_work(self):
        snap, candidate = ongoing_fixture()
        original = copy.deepcopy((snap, candidate))
        outcome = evaluate_scenario(snap, candidate, dict(scenario_id='ongoing',
            call_shocks={'RISK-001': {'available_at_shift_minutes': 300,
                                      'work_duration_multiplier': 2}}))
        self.assertEqual(outcome['physical_status'], 'FEASIBLE')
        self.assertEqual(outcome['commitment_status'], 'DISRUPTED')
        row = outcome['actual_schedules'][0]
        self.assertEqual(row['planned_start_at'], row['realized_start_at'])
        self.assertEqual(row['realized_completion_at'], '2026-11-02T04:00:00+09:00')
        self.assertEqual(row['work_intervals'][0],
            ['2026-11-02T01:00:00+09:00', '2026-11-02T02:00:00+09:00'])
        self.assertEqual(row['remaining_work_kg_at_as_of'], 60000)
        self.assertEqual(row['completed_work_kg_at_24h'] + row['remaining_work_kg_at_24h'],
                         row['cargo_quantity_kg'])
        self.assertEqual((snap, candidate), original)

    def test_faster_work_keeps_future_planned_start(self):
        snap, candidate = fixture()
        result = evaluate_scenario(snap, candidate, dict(scenario_id='faster',
            call_shocks={'RISK-001': {'work_duration_multiplier': '0.5'}}))
        self.assertEqual(result['status'], 'SUCCEEDED')
        rows = {r['call_id']: r for r in result['actual_schedules']}
        self.assertEqual(rows['RISK-001']['realized_completion_at'],
                         '2026-11-02T02:00:00+09:00')
        self.assertEqual(rows['RISK-002']['realized_start_at'],
                         rows['RISK-002']['planned_start_at'])
        self.assertEqual(result['commitment_status'], 'ON_PLAN')

    def test_frozen_faster_remaining_work_changes_completion(self):
        snap, candidate = ongoing_fixture()
        result = evaluate_scenario(snap, candidate, dict(scenario_id='faster-frozen',
            call_shocks={'RISK-001': {'work_duration_multiplier': '0.5'}}))
        self.assertEqual(result['physical_status'], 'FEASIBLE')
        self.assertEqual(result['commitment_status'], 'DISRUPTED')
        self.assertEqual(result['actual_schedules'][0]['realized_completion_at'],
                         '2026-11-02T02:30:00+09:00')

    def test_propagated_delays_and_strict_berth_release(self):
        snap, candidate = fixture()
        result = evaluate_scenario(snap, candidate, dict(scenario_id='late',
            call_shocks={'RISK-001': {'available_at_shift_minutes': 300}}))
        self.assertEqual(result['physical_status'], 'FEASIBLE')
        self.assertIn('RISK-002', result['propagated_delay_call_ids'])
        rows = result['actual_schedules']
        self.assertFalse(any(berth_overlap(a,b) for i,a in enumerate(rows)
                             for b in rows[i+1:]))
        for row in rows:
            self.assertEqual(row['completed_work_kg_at_72h'] +
                             row['remaining_work_kg_at_72h'], row['cargo_quantity_kg'])

    def test_earlier_arrival_preserves_planned_start_and_recomputes_wait(self):
        snap, candidate = fixture()
        result = evaluate_scenario(snap, candidate, dict(scenario_id='early',
            call_shocks={'RISK-001': {'available_at_shift_minutes': -30}}))
        row = next(r for r in result['vessel_results'] if r['call_id'] == 'RISK-001')
        self.assertEqual(row['realized_start_at'], row['planned_start_at'])
        self.assertEqual(row['realized_available_at'], '2026-11-02T00:30:00+09:00')
        self.assertEqual(row['total_wait_minutes'], 30)

    def test_seeded_100_fixture_is_repeatable_and_keeps_coverage(self):
        from pathlib import Path
        config = load_scenario_config(Path(__file__).resolve().parents[1] /
                                      'config/risk_synthetic_v1.json')
        config['sample_count'] = 100
        snap, candidate = fixture()
        scenarios = generate_scenarios(config, [r['call_id'] for r in candidate['vessel_results']])
        def run():
            return evaluate_with_adapter([dict(candidate, label=label) for label in 'ABC'],
                scenarios, lambda c,s: evaluate_scenario(snap,c,s))
        first, second = run(), run()
        self.assertEqual(first['risk_by_candidate'], second['risk_by_candidate'])
        self.assertEqual(first['disruption_by_candidate'], second['disruption_by_candidate'])
        self.assertEqual(first['scenario_count'], 100)
        self.assertEqual(first['coverage_by_candidate']['A']['scenario_count'], 100)


if __name__ == '__main__':
    unittest.main()
