import unittest
from src.daily_planning.horizon_policy import LEGACY_72H_V1, LOOKAHEAD_168H_V1, HorizonPolicy


class HorizonPolicyTest(unittest.TestCase):
    def test_boundaries_and_step(self):
        result = LOOKAHEAD_168H_V1.boundaries('2026-11-01T00:00:00+09:00')
        self.assertEqual(result['forecast_end_at'], '2026-11-08T00:00:00+09:00')
        self.assertEqual(result['optimization_end_at'], '2026-11-04T00:00:00+09:00')
        self.assertEqual(result['freeze_end_at'], '2026-11-02T00:00:00+09:00')
        self.assertEqual(result['next_run_at'], result['freeze_end_at'])
        self.assertEqual(LOOKAHEAD_168H_V1.inventory_interval_minutes, 30)

    def test_legacy_policy_and_invalid_step(self):
        self.assertEqual(LEGACY_72H_V1.boundaries('2026-11-01T00:00:00+09:00')['forecast_end_at'], '2026-11-04T00:00:00+09:00')
        with self.assertRaises(ValueError): HorizonPolicy('bad', 168, 72, 24, 12, 30)
        with self.assertRaises(ValueError): LOOKAHEAD_168H_V1.boundaries('2026-11-01T00:00:01+09:00')

if __name__ == '__main__': unittest.main()
