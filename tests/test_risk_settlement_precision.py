"""Source Excel minute-based settlement and USD-cent precision guards."""
import unittest
from datetime import datetime, timedelta

from contracts import settle_intervals


def contract(**changes):
    terms = dict(nor_at='2026-11-02T10:00:00', nor_valid='Y', notice_hours=0,
                 early_start_if_work='N', allowed_minutes=0, daily_demurrage_usd='7.20',
                 day_mode='SHINC', weather_policy='EXCLUDE',
                 post_demurrage_exceptions='NONE', despatch_policy='NONE', holidays=[])
    return dict(terms, **changes)


class RiskSettlementPrecisionTest(unittest.TestCase):
    def test_half_cent_demurrage_uses_usd_cent_round_half_up(self):
        start = datetime(2026, 11, 2, 10)
        end = start + timedelta(minutes=1)
        row = settle_intervals(contract(), start, end, [(start, end, 'WAIT')])
        self.assertEqual(row['demurrage_minutes'], 1)
        self.assertEqual(row['demurrage_cents'], 1)

    def test_contract_exception_applies_after_demurrage(self):
        start = datetime(2026, 11, 2, 10)
        end = start + timedelta(minutes=10)
        row = settle_intervals(contract(post_demurrage_exceptions='OWNER_ONLY'),
                               start, end, [(start, end, 'OWNER_BREAKDOWN')])
        self.assertEqual(row['demurrage_cents'], 0)
        self.assertEqual(row['post_excluded_minutes'], 10)

    def test_existing_contract_engine_rejects_unresolved_subminute(self):
        start = datetime(2026, 11, 2, 10)
        end = start + timedelta(seconds=61)
        with self.assertRaisesRegex(ValueError, 'whole minutes'):
            settle_intervals(contract(), start, end, [(start, end, 'WAIT')])


if __name__ == '__main__':
    unittest.main()
