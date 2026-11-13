"""Historical clock-audit scope and fail-closed settlement regression tests."""
import copy
import json
import unittest

from src.daily_planning.integrated_v3.v5_settlement import AUDIT, clock_audit, settle_v5_call
from src.daily_planning.physical_scheduling.phases import replay
from tests.test_v5_cost_search import fixture, record, START


class SourceFingerprintSettlementTests(unittest.TestCase):
    def setUp(self):
        self.audit = json.loads(AUDIT.read_bytes())
        self.affected = self.audit['clock_start_divergences'][0]['contract_id']
        self.source_hash = self.audit['source_workbook_sha256']
        self.snapshot, policy = fixture()
        call = self.snapshot['calls'][0]
        call['call_id'] = self.affected
        self.snapshot['workbook_tables']['contracts']['rows'][0]['case_id'] = self.affected
        self.row = replay(call, self.snapshot['berths'][0], '2026-11-03T09:00:00+09:00', policy)
        self.evidence = record(self.row)

    def test_matching_source_requires_clock_audit(self):
        self.snapshot['source_input']['source_workbook_sha256'] = self.source_hash
        audit = clock_audit(self.snapshot)
        self.assertEqual(audit['status'], 'NOT_REVALIDATABLE_WITH_AVAILABLE_V5_FIELDS')
        self.assertIn(self.affected, audit['historical_divergence_ids_in_v5'])
        result = settle_v5_call(self.snapshot, self.row, as_of_at=START, evidence=self.evidence)
        self.assertEqual(result['status'], 'SETTLEMENT_UNRESOLVED')
        self.assertEqual(result['issues'][0]['code'], 'CLOCK_START_AUDIT_PENDING')
        self.assertNotIn('demurrage_cents', result)

    def test_missing_source_fingerprint_fails_closed(self):
        audit = clock_audit(self.snapshot)
        self.assertEqual(audit['status'], 'CURRENT_SOURCE_FINGERPRINT_MISSING')
        self.assertIn(self.affected, audit['historical_divergence_ids_in_v5'])
        result = settle_v5_call(self.snapshot, self.row, as_of_at=START, evidence=self.evidence)
        self.assertEqual(result['issues'][0]['code'], 'CLOCK_START_AUDIT_PENDING')

    def test_different_source_does_not_apply_historical_audit(self):
        self.snapshot['source_input']['source_workbook_sha256'] = '0' * 64
        self.assertNotEqual(self.source_hash, '0' * 64)
        original = copy.deepcopy(self.snapshot)
        audit = clock_audit(self.snapshot)
        self.assertEqual(audit['status'], 'HISTORICAL_AUDIT_NOT_APPLICABLE_DIFFERENT_SOURCE')
        self.assertEqual(audit['historical_divergence_ids_in_v5'], [])
        result = settle_v5_call(self.snapshot, self.row, as_of_at=START, evidence=self.evidence)
        self.assertEqual(result['status'], 'SETTLED', result)
        self.assertEqual(result['demurrage_cents'], 54100)
        self.assertEqual(self.snapshot, original)


if __name__ == '__main__':
    unittest.main()
