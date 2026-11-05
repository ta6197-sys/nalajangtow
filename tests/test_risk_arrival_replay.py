"""Small fixed-order arrival-shock replay and failure accounting tests."""
from __future__ import annotations

import copy
import json
import unittest
from datetime import timedelta
from pathlib import Path

from src.daily_planning.context import local_time, signature
from src.daily_planning.evaluation import evaluate_baseline
from src.daily_planning.input_loader import fingerprint
from src.daily_planning.risk.replay import evaluate_scenario
from src.daily_planning.risk.service import evaluate_with_adapter
from weather_optimizer import WeatherSeries

from .test_risk_replay import WEATHER_PATH, snapshot

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/risk_abc'


def fixture():
    snap = snapshot()
    snap['calls'] = []
    snap['call_states'] = []
    plan = []
    for index, hour in enumerate((1, 4, 7), 1):
        cid = f'RISK-{index:03d}'
        call = copy.deepcopy(snapshot()['calls'][0])
        state = copy.deepcopy(snapshot()['call_states'][0])
        call.update(call_id=cid, available_at=f'2026-11-02T{hour:02d}:00:00+09:00')
        state['call_id'] = cid
        snap['calls'].append(call)
        snap['call_states'].append(state)
        plan.append(dict(call_id=cid, berth_id='BERTH-01', start_at=call['available_at']))
    snap['snapshot_hash'] = signature({k: v for k, v in snap.items() if k != 'snapshot_hash'})
    baseline = evaluate_baseline(snap, plan, WeatherSeries.from_csv(WEATHER_PATH))['evaluation']
    candidate = dict(candidate_id='A', input_snapshot_hash=snap['snapshot_hash'],
                     cost_cents=baseline['summary']['demurrage_increment_to_completion_cents'],
                     wait_minutes=baseline['summary']['total_wait_minutes'],
                     vessel_results=baseline['vessel_results'])
    return snap, candidate


def shock(**shifts):
    return dict(scenario_id='arrival-small', call_shocks={
        cid: dict(available_at_shift_minutes=minutes, work_duration_multiplier=1.0)
        for cid, minutes in shifts.items()})


def saved_candidate(label):
    snap = json.loads((FIXTURE / 'snapshot.json').read_text())
    snap['weather'] = fingerprint(WEATHER_PATH)
    snap['snapshot_hash'] = signature({k: v for k, v in snap.items() if k != 'snapshot_hash'})
    stored = json.loads((FIXTURE / f'candidate_{label}.json').read_text())
    candidate = dict(candidate_id=label, input_snapshot_hash=snap['snapshot_hash'],
                     cost_cents=stored['cost_cents'], wait_minutes=stored['wait_minutes'],
                     vessel_results=stored['vessel_results'])
    return snap, candidate


class ArrivalReplayTest(unittest.TestCase):
    def test_zero_shift_matches_three_candidate_baselines_to_the_cent(self):
        snap, _ = fixture()
        weather = WeatherSeries.from_csv(WEATHER_PATH)
        for label, offset in (('A', 0), ('B', 30), ('C', 60)):
            plan = []
            for call in snap['calls']:
                start = local_time(call['available_at']) + timedelta(minutes=offset)
                plan.append(dict(call_id=call['call_id'], berth_id='BERTH-01',
                                 start_at=start.isoformat()))
            baseline = evaluate_baseline(snap, plan, weather)['evaluation']
            candidate = dict(candidate_id=label, input_snapshot_hash=snap['snapshot_hash'],
                             cost_cents=baseline['summary']['demurrage_increment_to_completion_cents'],
                             wait_minutes=baseline['summary']['total_wait_minutes'],
                             vessel_results=baseline['vessel_results'])
            result = evaluate_scenario(snap, candidate, shock())
            self.assertEqual(result['status'], 'SUCCEEDED')
            self.assertEqual(result['cost_cents'], candidate['cost_cents'])
            self.assertEqual(result['vessel_results'], baseline['vessel_results'])

    def test_saved_abc_evaluations_match_zero_shift_to_the_cent(self):
        for label in 'ABC':
            snap, candidate = saved_candidate(label)
            result = evaluate_scenario(snap, candidate, shock())
            self.assertEqual(result['status'], 'SUCCEEDED')
            self.assertEqual(result['cost_cents'], candidate['cost_cents'])
            self.assertEqual(result['vessel_results'], candidate['vessel_results'])

    def test_daylight_candidate_delay_replays_work_and_contract(self):
        from src.daily_planning.daylight import daylight_window
        snap, candidate = saved_candidate('A')
        result = evaluate_scenario(snap, candidate, shock(**{'DEM-0011': 1440}))
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertGreater(result['cost_cents'], candidate['cost_cents'])
        row = next(r for r in result['vessel_results'] if r['call_id'] == 'DEM-0011')
        for start, end in row['work_intervals']:
            sunrise, sunset = daylight_window(start)
            self.assertLessEqual(sunrise, local_time(start))
            self.assertLessEqual(local_time(end), sunset)
        source = next(c for c in snap['calls'] if c['call_id'] == row['call_id'])
        worked = sum(int((local_time(end) - local_time(start)).total_seconds() // 60)
                     for start, end in row['work_intervals'])
        self.assertEqual(worked, source['work_minutes'])
        self.assertGreaterEqual(row['remaining_work_kg_at_72h'], 0)
        self.assertLessEqual(row['remaining_work_kg_at_72h'], source['cargo_quantity_kg'])

    def test_positive_shift_propagates_without_berth_overlap_or_lost_work(self):
        snap, candidate = fixture()
        unchanged = copy.deepcopy((snap, candidate))
        result = evaluate_scenario(snap, candidate, shock(**{'RISK-001': 300}))
        self.assertEqual(result['status'], 'SUCCEEDED')
        rows = sorted(result['vessel_results'], key=lambda r: r['work_start_at'])
        self.assertEqual([local_time(r['work_start_at']).hour for r in rows], [6, 8, 10])
        for left, right in zip(rows, rows[1:]):
            self.assertLessEqual(local_time(left['berth_release_at']), local_time(right['work_start_at']))
        self.assertEqual(sum(r['demurrage_increment_to_completion_cents'] for r in rows), result['cost_cents'])
        self.assertEqual(sum(r['remaining_work_kg_at_24h'] for r in rows), 360000)
        for row in rows:
            elapsed = int((local_time(row['completion_at']) - local_time(row['work_start_at'])).total_seconds() // 60)
            worked = elapsed - sum(int((local_time(end) - local_time(start)).total_seconds() // 60)
                                   for start, end in row['work_pause_intervals'])
            self.assertEqual(worked, 120)
            self.assertEqual(row['remaining_work_kg_at_72h'], 0)
        self.assertEqual((snap, candidate), unchanged)

    def test_negative_shift_keeps_existing_start_and_order(self):
        snap, candidate = fixture()
        result = evaluate_scenario(snap, candidate, shock(**{'RISK-001': -60}))
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertEqual(result['cost_cents'], candidate['cost_cents'])
        self.assertEqual([r['work_start_at'] for r in result['vessel_results']],
                         [r['work_start_at'] for r in candidate['vessel_results']])

    def test_frozen_arrival_conflict_is_explicit_failure_and_stops_statistics(self):
        snap = snapshot()
        baseline = evaluate_baseline(snap, [dict(call_id='RISK-001', berth_id='BERTH-01',
                                                 start_at=snap['calls'][0]['available_at'])],
                                     WeatherSeries.from_csv(WEATHER_PATH))['evaluation']
        candidate = dict(candidate_id='A', input_snapshot_hash=snap['snapshot_hash'],
                         cost_cents=baseline['summary']['demurrage_increment_to_completion_cents'],
                         wait_minutes=baseline['summary']['total_wait_minutes'],
                         vessel_results=baseline['vessel_results'], label='A')
        scenario = shock(**{'RISK-001': 1})
        result = evaluate_scenario(snap, candidate, scenario)
        self.assertEqual(result['status'], 'FAILED')
        self.assertIn('FROZEN_ASSIGNMENT_CONFLICT', result['failure_reason'])
        report = evaluate_with_adapter([candidate], [dict(scenario, scenario_weight='1')],
                                       lambda c, s: evaluate_scenario(snap, c, s))
        self.assertEqual(report['risk_status'], 'FAILED_SCENARIOS')
        self.assertIsNone(report['risk_by_candidate'])
        self.assertEqual(report['failures'][0]['scenario_id'], scenario['scenario_id'])


if __name__ == '__main__':
    unittest.main()
