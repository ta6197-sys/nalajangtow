import copy
import json
from pathlib import Path
import tempfile
import unittest

from src.daily_planning.facility_master import build_facility_master

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/sk_facilities.json'


class FacilityMasterTest(unittest.TestCase):
    def test_explicit_selection_keeps_provenance_and_no_fixed_count(self):
        result = build_facility_master(SOURCE, active_facility_codes=['SK#4-2', 'SK#4-1'])
        self.assertEqual([r['facility_code'] for r in result['active_facilities']], ['SK#4-1', 'SK#4-2'])
        self.assertTrue(all(r['workbook_row'] > 0 for r in result['active_facilities']))
        self.assertEqual(len(result['source']['data_sha256']), 64)
        self.assertFalse(result['operational_approval'])
        self.assertIn('PILOTAGE', result['pending_rules'])
        self.assertEqual(len(build_facility_master(SOURCE, active_facility_codes=['SK#3'])['active_facilities']), 1)

    def test_missing_or_invalid_selection_fails_closed(self):
        for values in ([], ['SK#3', 'SK#3'], ['not-a-facility']):
            with self.subTest(values=values), self.assertRaises(ValueError):
                build_facility_master(SOURCE, active_facility_codes=values)

    def test_replacement_changes_source_hash_and_preserves_provenance(self):
        original = json.loads(SOURCE.read_text())
        modified = copy.deepcopy(original)
        modified['facilities'][0]['note'] = 'SYNTHETIC replacement fixture'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'replacement.json'
            path.write_text(json.dumps(modified, ensure_ascii=False))
            old = build_facility_master(SOURCE, active_facility_codes=['SKB#2'])
            new = build_facility_master(path, active_facility_codes=['SKB#2'])
            self.assertNotEqual(old['source']['data_sha256'], new['source']['data_sha256'])
            self.assertEqual(old['source']['source_workbook_sha256'], new['source']['source_workbook_sha256'])

if __name__ == '__main__': unittest.main()
