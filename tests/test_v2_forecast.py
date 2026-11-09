import copy
import unittest

from src.daily_planning.context import signature
from src.daily_planning.forecast.service import generate_forecast
from benchmarks.v2_phase0 import fixture


def sign(snapshot):
    snapshot['snapshot_hash'] = signature({k: v for k, v in snapshot.items() if k != 'snapshot_hash'})
    return snapshot


def source():
    snapshot, _ = fixture(2)
    for call in snapshot['calls']:
        call['known_at'] = '2026-10-31T00:00:00+09:00'
    return sign(snapshot)


class ForecastTest(unittest.TestCase):
    def test_read_only_deterministic_and_missing_tentative_provenance(self):
        snap = source()
        snap['previous_tentative_assignments'] = [{'call_id': 'SYN-000', 'berth_id': 'B0',
            'start_at': '2026-11-02T00:00:00+09:00', 'completion_at': '2026-11-02T01:00:00+09:00'}]
        sign(snap); before = copy.deepcopy(snap)
        result = generate_forecast(snap)
        self.assertEqual(snap, before)
        self.assertEqual(result, generate_forecast(snap))
        self.assertEqual(result['occupancy'], [])
        self.assertIn({'call_id': 'SYN-000', 'field': 'tentative_known_at'}, result['missing_data'])

    def test_future_disclosures_and_forecast_end_excluded(self):
        snap = source()
        snap['calls'][0]['known_at'] = '2026-11-01T00:01:00+09:00'
        snap['calls'][1]['available_at'] = '2026-11-08T00:00:00+09:00'
        result = generate_forecast(sign(snap))
        self.assertEqual(result['vessels'], [])
        self.assertEqual(result['unassigned_call_ids'], [])

    def test_confirmed_and_known_tentative_conflicts_are_distinguished(self):
        snap = source()
        snap['commits'] = [{'call_id': 'SYN-000', 'berth_id': 'B0', 'confirmed_at': '2026-10-31T00:00:00+09:00',
                            'locked_from_at': '2026-11-01T00:00:00+09:00', 'locked_until_at': '2026-11-04T01:00:00+09:00'}]
        snap['previous_tentative_assignments'] = [{'call_id': 'SYN-001', 'berth_id': 'B0', 'known_at': '2026-10-31T01:00:00+09:00',
             'start_at': '2026-11-03T00:00:00+09:00', 'completion_at': '2026-11-03T03:00:00+09:00'}]
        result = generate_forecast(sign(snap))
        self.assertEqual(len(result['occupancy']), 2)
        self.assertEqual(result['conflicts'][0]['certainty'], 'POTENTIAL')
        self.assertEqual(result['occupancy'][0]['end_at'], '2026-11-04T01:00:00+09:00')

    def test_full_berth_occupancy_marks_potential_congestion(self):
        snap = source()
        snap['previous_tentative_assignments'] = [
            {'call_id': 'SYN-000', 'berth_id': 'B0', 'known_at': '2026-10-31T00:00:00+09:00',
             'start_at': '2026-11-01T01:00:00+09:00', 'completion_at': '2026-11-01T03:00:00+09:00'},
            {'call_id': 'SYN-001', 'berth_id': 'B1', 'known_at': '2026-10-31T00:00:00+09:00',
             'start_at': '2026-11-01T02:00:00+09:00', 'completion_at': '2026-11-01T04:00:00+09:00'},
        ]
        periods = generate_forecast(sign(snap))['congestion_periods']
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0]['start_at'], '2026-11-01T02:00:00+09:00')
        self.assertEqual(periods[0]['end_at'], '2026-11-01T03:00:00+09:00')
        self.assertEqual(periods[0]['certainty'], 'POTENTIAL')

    def test_missing_disclosure_or_future_confirmation_fails_closed(self):
        snap = source(); snap['calls'][0].pop('known_at'); sign(snap)
        with self.assertRaisesRegex(ValueError, 'availability'): generate_forecast(snap)
        snap = source(); snap['commits'] = [{'confirmed_at': '2026-11-01T01:00:00+09:00'}];sign(snap)
        with self.assertRaisesRegex(ValueError, 'not known'): generate_forecast(snap)

    def test_uncertain_predictions_require_known_provenance_and_do_not_assign_berths(self):
        snap = source()
        snap['uncertain_forecasts'] = [
            {'forecast_id': 'visible', 'known_at': '2026-10-31T00:00:00+09:00', 'predicted_at': '2026-11-03T00:00:00+09:00', 'source': 'SYNTHETIC_FIXTURE'},
            {'forecast_id': 'future', 'known_at': '2026-11-01T01:00:00+09:00', 'predicted_at': '2026-11-04T00:00:00+09:00', 'source': 'SYNTHETIC_FIXTURE'},
            {'forecast_id': 'missing', 'predicted_at': '2026-11-04T00:00:00+09:00'},
        ]
        result = generate_forecast(sign(snap))
        self.assertEqual([row['forecast_id'] for row in result['uncertain_forecasts']], ['visible'])
        self.assertEqual(result['occupancy'], [])
        self.assertIn({'field': 'uncertain_forecast_provenance'}, result['missing_data'])

    def test_future_forecasts_do_not_change_visible_projection(self):
        baseline = source()
        changed = copy.deepcopy(baseline)
        changed['uncertain_forecasts'] = [{'forecast_id': 'future', 'known_at': '2026-11-02T00:00:00+09:00',
                                            'predicted_at': '2026-11-03T00:00:00+09:00'}]
        baseline['uncertain_forecasts'] = []
        first, second = generate_forecast(sign(baseline)), generate_forecast(sign(changed))
        first.pop('snapshot_hash'); second.pop('snapshot_hash')
        self.assertEqual(first, second)

    def test_mutation_of_snapshot_is_detected(self):
        snap = source();snap['calls'][0]['available_at'] = '2026-11-05T00:00:00+09:00'
        with self.assertRaisesRegex(ValueError, 'hash mismatch'): generate_forecast(snap)

if __name__ == '__main__': unittest.main()
