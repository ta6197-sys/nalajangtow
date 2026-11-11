import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from src.daily_planning.integrated_input.loader import load_inputs
from src.daily_planning.integrated_input.normalizer import normalize
from src.daily_planning.integrated_input.v5 import validate_v5
from src.daily_planning.integrated_input.cli import run

ROOT=Path(os.environ.get('DOCKTIMIZE_V5_DATA_DIR',Path(__file__).resolve().parents[2]/'data/Docktimize_v5_input'))

@unittest.skipUnless(ROOT.is_dir(),'Supplied v5 input files unavailable')
class V5InputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle=load_inputs(ROOT,scenario='V5_3TANK')
    def setUp(self):self.bundle=copy.deepcopy(self.bundle)
    def report(self):return validate_v5(normalize(self.bundle))
    def rejects(self, substring, call_id=None, tank_id=None):
        r=self.report();self.assertEqual(r['status'],'INPUT_INVALID')
        self.assertTrue(any(substring.lower() in e['message'].lower() and (call_id is None or e.get('call_id')==call_id) and (tank_id is None or e.get('tank_id')==tank_id) for e in r['errors']),r['errors'][:5])
    def test_actual_inputs_and_reports(self):
        files=[next(p for p in ROOT.iterdir() if p.name==a['filename']) for a in self.bundle['artifacts']]
        before=[hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        s=normalize(self.bundle);r=validate_v5(s)
        self.assertEqual(r['input_status'],'INPUT_VALID',r['errors']);self.assertEqual(r['status'],'OPERATIONAL_POLICY_PENDING')
        self.assertEqual((r['vessel_count'],r['berth_count'],r['tank_count'],r['hard_fix_count'],r['ordinary_call_count'],r['flow_record_count']),(140,11,3,15,125,696))
        self.assertTrue(all(c['physical'] and c['eligible_berth_ids'] for c in s['calls']))
        self.assertTrue(all(c['fix_etb_at'] is None for c in s['calls'] if not c['fixed_etb_is_hard']))
        self.assertEqual(s['fixed_process_flows'],self.bundle['flows'])
        with tempfile.TemporaryDirectory() as out:
            report,manifest=run(ROOT,out,scenario='V5_3TANK');self.assertEqual(report,r)
            path=Path(out)/manifest['run_id'];self.assertEqual(json.loads((path/'validation_report.json').read_text()),r)
            self.assertEqual(json.loads((path/'normalized_snapshot.json').read_text()),s)
            self.assertEqual(run(ROOT,out,scenario='V5_3TANK')[0],r)
        self.assertEqual(before,[hashlib.sha256(p.read_bytes()).hexdigest() for p in files])
    def test_vessel_id_mismatch(self):
        p=self.bundle['source']['vessel_profiles'][0];p['vessel_id']='BAD';self.rejects('identity mismatch',call_id=p['case_id'])
    def test_missing_profile(self):
        p=self.bundle['source']['vessel_profiles'].pop();self.rejects('Missing or duplicate vessel profile',call_id=p['case_id'])
    def test_conversion_mismatch(self):
        c=self.bundle['source']['cargo_calls'][0];c['cargo_volume_bbl']='1';self.rejects('conversion mismatch',call_id=c['call_id'])
    def test_unconnected_tank(self):
        self.bundle['source']['tanks'][0]['connected_berth_ids']=[];self.rejects('Tank has no connected berth',tank_id='TANK-01')
    def test_fix_before_eta(self):
        c=next(c for c in self.bundle['source']['cargo_calls'] if c['fixed_etb_is_hard']);c['fix_etb_at']='2026-01-01T00:00+09:00';self.rejects('precedes ETA',call_id=c['call_id'])
    def test_flow_gap(self):
        self.bundle['flows']['segments'].pop(0);self.rejects('gap or overlap',tank_id='TANK-01')
    def test_flow_overlap(self):
        self.bundle['flows']['segments'].append(copy.deepcopy(self.bundle['flows']['segments'][0]));self.rejects('gap or overlap',tank_id='TANK-01')
    def test_over_capacity(self):
        self.bundle['source']['tanks'][0]['initial_inventory_bbl']='10000001';self.rejects('Invalid tank',tank_id='TANK-01')
    def test_below_minimum(self):
        self.bundle['source']['tanks'][0]['initial_inventory_bbl']='999999';self.rejects('Invalid tank',tank_id='TANK-01')
    def test_workbook_mismatch(self):
        self.bundle['tables']['profiles']['rows'][0]['dwt_mt']=1;self.rejects('Workbook vessel profile mismatch',call_id='DEM-0001')
    def test_duplicate_profile(self):
        self.bundle['source']['vessel_profiles'].append(copy.deepcopy(self.bundle['source']['vessel_profiles'][0]));self.rejects('duplicate profile call_id',call_id='DEM-0001')
    def test_flow_sign_mismatch(self):
        self.bundle['flows']['segments'][0]['fixed_net_flow_bbl_per_hour']='600';self.rejects('Workbook flow mismatch',tank_id='TANK-01')
    def test_workbook_fingerprint_mismatch(self):
        self.bundle['source']['source_workbook_sha256']='0'*64;self.rejects('SHA-256')
    def test_assigned_ordinary_berth(self):
        c=next(c for c in self.bundle['source']['cargo_calls'] if not c['fixed_etb_is_hard']);c['assigned_berth_id']='BERTH-01';self.rejects('preassignment',call_id=c['call_id'])

if __name__=='__main__':unittest.main()
