"""No DB, weather, OR-Tools or external server required."""
import json
import tempfile
import unittest
from pathlib import Path
from src.daily_planning.berth_rules import load_facilities
from src.daily_planning.sk_preflight import (SKPreflightError, EXPECTED_BERTHS,
    load_call_profiles, audit_mapping, audit_calls, create_blank_profiles)


class SKPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facilities = load_facilities()
        cls.codes = list(cls.facilities)

    def valid_mapping(self):
        # TEST ONLY. Not a valid production mapping and never written to config.
        return dict(zip(EXPECTED_BERTHS, self.codes[:8]))

    def test_empty_mapping_blocks(self):
        x = {bid: None for bid in EXPECTED_BERTHS}
        self.assertEqual(audit_mapping(x, self.facilities)['status'], 'NOT_READY')

    def test_missing_mapping_key_blocks(self):
        x = self.valid_mapping(); x.pop('BERTH-01')
        self.assertEqual(audit_mapping(x, self.facilities)['status'], 'NOT_READY')

    def test_bad_facility_code_blocks(self):
        x = self.valid_mapping(); x['BERTH-01'] = 'DOES_NOT_EXIST'
        self.assertEqual(audit_mapping(x, self.facilities)['status'], 'NOT_READY')

    def test_duplicate_physical_facility_blocks(self):
        x = self.valid_mapping(); x['BERTH-02'] = x['BERTH-01']
        self.assertEqual(audit_mapping(x, self.facilities)['status'], 'NOT_READY')

    def test_valid_mapping_structure(self):
        self.assertEqual(audit_mapping(self.valid_mapping(), self.facilities)['status'], 'READY')

    def test_mapping_missing_profiles_blocks(self):
        x = audit_calls(self.valid_mapping(), self.facilities, {}, ['CALL-1'])
        self.assertEqual(x['calls'][0]['reason'], 'VESSEL_PROFILE_MISSING')

    def test_mapping_not_ready_blocks_even_valid_vessel(self):
        x = audit_calls({bid: None for bid in EXPECTED_BERTHS}, self.facilities,
                        {'C': {'cargo_type': '원유', 'draft_m': 20, 'dwt': 100000}}, ['C'])
        self.assertEqual(x['calls'][0]['reason'], 'BERTH_MAPPING_NOT_READY')

    def test_unknown_draft_not_pass_for_crude_sbm(self):
        x = audit_calls(self.valid_mapping(), self.facilities,
                        {'C': {'cargo_type': '원유', 'draft_m': None, 'dwt': 100000}}, ['C'])
        self.assertNotIn('BERTH-01', x['calls'][0]['eligible_berths'])

    def test_supported_crude_compatible_with_sbm(self):
        x = audit_calls(self.valid_mapping(), self.facilities,
                        {'C': {'cargo_type': '원유', 'draft_m': 20, 'dwt': 100000}}, ['C'])
        self.assertEqual(x['calls'][0]['status'], 'READY')
        self.assertIn('BERTH-01', x['calls'][0]['eligible_berths'])
        self.assertEqual(x['scope'], 'PHYSICAL_ONLY_NOT_OPERATIONAL_CLEARANCE')

    def test_excessive_draft_rejected_for_sbm(self):
        x = audit_calls(self.valid_mapping(), self.facilities,
                        {'C': {'cargo_type': '원유', 'draft_m': 25, 'dwt': 100000}}, ['C'])
        self.assertNotIn('BERTH-01', x['calls'][0]['eligible_berths'])
        self.assertNotIn('BERTH-02', x['calls'][0]['eligible_berths'])

    def test_missing_cargo_not_pass(self):
        x = audit_calls(self.valid_mapping(), self.facilities,
                        {'C': {'draft_m': 20, 'dwt': 100000}}, ['C'])
        self.assertEqual(x['summary']['ready'], 0)

    def test_all_deferred_hazard_detected(self):
        x = audit_calls(self.valid_mapping(), self.facilities,
                        {'C': {'cargo_type': '원유', 'draft_m': None, 'dwt': None}}, ['C'])
        self.assertFalse(x['summary']['ready_for_strict_physical_filter'])

    def test_duplicate_call_ids_error(self):
        with self.assertRaises(SKPreflightError):
            audit_calls(self.valid_mapping(), self.facilities, {}, ['C', 'C'])

    def test_blank_profiles_have_no_fake_dimensions(self):
        t = create_blank_profiles(['DEM-0001'])
        self.assertIsNone(t['calls'][0]['draft_m'])
        self.assertEqual(t['calls'][0]['data_origin'], 'UNVERIFIED')

    def test_profile_loader_duplicate_errors(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'profiles.json'
            path.write_text(json.dumps({'schema_version':1,'calls':[{'call_id':'A'},{'call_id':'A'}]}))
            with self.assertRaises(SKPreflightError):
                load_call_profiles(path)

    def test_profile_loader_invalid_negative(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'profiles.json'
            path.write_text(json.dumps({'schema_version':1,'calls':[{'call_id':'A','dwt':-1}]}))
            with self.assertRaises(SKPreflightError):
                load_call_profiles(path)

    def test_profile_loader_invalid_nan(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'profiles.json'
            path.write_text(json.dumps({'schema_version':1,'calls':[{'call_id':'A','dwt':'NaN'}]}))
            with self.assertRaises(SKPreflightError):
                load_call_profiles(path)

    def test_profile_loader_null_allowed_but_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'profiles.json'
            path.write_text(json.dumps(create_blank_profiles(['A']), ensure_ascii=False))
            self.assertIsNone(load_call_profiles(path)['A']['draft_m'])


if __name__ == '__main__':
    unittest.main()
