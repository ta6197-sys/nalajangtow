import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from src.daily_planning.integrated_input.loader import load_inputs,InputError,FILES
from src.daily_planning.integrated_input.normalizer import normalize
from src.daily_planning.integrated_input.validator import validate

FIXTURE=Path(__file__).parent/'fixtures/integrated_input_v3/small_synthetic_bundle.json'
PROFILE={'calls':1,'berths':1,'tanks':1,'fix':0,'segments':1,'capacity':175000}

class V3InputTest(unittest.TestCase):
    def setUp(self):self.bundle=json.loads(FIXTURE.read_text());self.snapshot=normalize(self.bundle)
    def codes(self):return {r['code'] for r in validate(self.snapshot,PROFILE)['issues']}
    def test_valid_small_profile(self):self.assertTrue(validate(self.snapshot,PROFILE)['data_preflight_pass'])
    def test_missing_required_file(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(InputError) as exc:load_inputs(folder)
            self.assertEqual(exc.exception.code,'MISSING_INPUT');self.assertIn(FILES[0],str(exc.exception))
    def test_missing_json_is_reported_before_reading_workbook(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/FILES[0]).write_bytes(b'not read before file inventory')
            (Path(folder)/FILES[2]).write_text('{}')
            with self.assertRaises(InputError) as exc:load_inputs(folder)
            self.assertEqual(str(exc.exception),FILES[1])

    def test_large_contract_mismatch_is_not_rounding(self):
        self.snapshot['calls'][0]['allowed_hours']='1000'
        self.assertIn('INVALID_SCHEMA',self.codes())

    def test_duplicate_vessel(self):
        self.snapshot['calls'].append(copy.deepcopy(self.snapshot['calls'][0]));self.assertIn('INVALID_SCHEMA',self.codes())
    def test_duplicate_vessel_with_distinct_call_id(self):
        row=copy.deepcopy(self.snapshot['calls'][0]);row['call_id']='SYNTHETIC-DUPLICATE-VESSEL'
        self.snapshot['calls'].append(row)
        result=validate(self.snapshot,PROFILE)
        self.assertTrue(any(i['source_field']=='vessel_id' for i in result['issues']))

    def test_unknown_berth(self):
        self.snapshot['tanks'][0]['connected_berth_ids']=['UNKNOWN'];self.assertIn('UNKNOWN_REFERENCE',self.codes())
    def test_unknown_tank(self):
        self.snapshot['berths'][0]['connected_tank_ids']='UNKNOWN';self.assertIn('UNKNOWN_REFERENCE',self.codes())
    def test_capacity_sum(self):
        self.snapshot['tanks'][0]['capacity_bbl']='175001';self.assertIn('INVALID_QUANTITY',self.codes())
    def test_missing_fix(self):
        self.snapshot['calls'][0]['fixed_etb_is_hard']=True;self.assertIn('FIX_PRECHECK_FAILED',self.codes())
    def test_nonfix_timestamp(self):
        self.snapshot['calls'][0]['fix_etb_at']='2026-12-11T18:00+09:00';self.assertIn('FIX_PRECHECK_FAILED',self.codes())
    def test_missing_physical(self):
        self.snapshot['calls'][0]['physical'].pop('loa_m');self.assertIn('MISSING_PHYSICAL_DATA',self.codes())
    def test_flow_overlap(self):
        self.snapshot['fixed_process_flows']['segments']*=2;self.assertIn('FLOW_GAP',self.codes())
    def test_flow_gap(self):
        self.snapshot['fixed_process_flows']['segments']=[];self.assertIn('FLOW_GAP',self.codes())
    def test_timezone(self):
        self.snapshot['calls'][0]['eta_at']='2026-11-03T00:00';self.assertIn('INVALID_TIME',self.codes())
    def test_mass_volume(self):
        self.snapshot['calls'][0]['cargo_volume_bbl']='1';self.assertIn('INVALID_QUANTITY',self.codes())
    def test_no_baseline_constraints_or_mutation(self):
        before=copy.deepcopy(self.bundle);s=normalize(self.bundle);validate(s,PROFILE)
        self.assertEqual(before,self.bundle);self.assertNotIn('fixed_berth_id',s['calls'][0]);self.assertIsNone(s['calls'][0]['fix_etb_at'])
    def test_legacy_eight_berth_mapping_unchanged(self):
        from src.daily_planning.sk_physical_v3 import EXPECTED_MAPPING
        self.assertEqual(len(EXPECTED_MAPPING),8)


class SuppliedV3FixtureTest(unittest.TestCase):
    def test_actual_fixture_hashes_dry_run_and_reproducible_artifacts(self):
        import os,unicodedata,importlib.util
        if importlib.util.find_spec('openpyxl') is None:self.skipTest('Optional openpyxl unavailable')
        root=Path(os.environ.get('DOCKTIMIZE_V3_DATA_DIR',Path(__file__).resolve().parents[2]/'data'))
        if not root.is_dir() or not all(any(unicodedata.normalize('NFC',p.name)==n for p in root.iterdir()) for n in FILES):self.skipTest('Supplied V3 files unavailable')
        from src.daily_planning.integrated_input.cli import run
        paths=[next(p for p in root.iterdir() if unicodedata.normalize('NFC',p.name)==n) for n in FILES]
        before=[hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'outputs'
            report,manifest=run(root,target,True)
            self.assertFalse(target.exists())
            self.assertEqual(report['counts'],{'calls':140,'berths':11,'tanks':30,'fix':15,'segments':6960})
            self.assertTrue(report['data_preflight_pass'],report['issues'])
            self.assertNotEqual(manifest['historical_parent_source_workbook_sha256'],manifest['artifacts'][0]['sha256'])
            a,m=run(root,target);b,n=run(root,target)
            self.assertEqual(a,b);self.assertEqual(m['run_id'],n['run_id'])
            self.assertEqual(len(list(target.iterdir())),1)
        self.assertEqual(before,[hashlib.sha256(p.read_bytes()).hexdigest() for p in paths])

if __name__=='__main__':unittest.main()
