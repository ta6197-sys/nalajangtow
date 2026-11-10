import copy
from datetime import datetime,timedelta
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from src.daily_planning.integrated_input.flow_extension import build_extended_snapshot,validate_extended_snapshot,check_extension_available

class FlowExtensionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        start=datetime.fromisoformat('2026-12-17T00:00:00+09:00');segments=[]
        for i in range(56):
            a=start+timedelta(hours=6*i);segments.append({'tank_id':'T','start_at':a.isoformat(),'end_at':(a+timedelta(hours=6)).isoformat(),'fixed_net_flow_bbl_per_hour':str(i)})
        base={'segments':segments,'record_count':56,'flow_horizon_end_at':'2026-12-31T00:00:00+09:00'};self.base=self.root/'base.json';self.base.write_text(json.dumps(base));sha=sha256(self.base.read_bytes()).hexdigest();self.snapshot={'tanks':[{'tank_id':'T'}],'fixed_process_flows':copy.deepcopy(base),'source_flow_input':copy.deepcopy(base)}
        a=datetime.fromisoformat('2026-12-31T00:00:00+09:00');known='2026-11-10T23:13:27+09:00';source='SYNTHETIC_BASE:'+sha
        extra=[{'tank_id':'T','start_at':(a+timedelta(hours=6*i)).isoformat(),'end_at':(a+timedelta(hours=6*(i+1))).isoformat(),'fixed_net_flow_bbl_per_hour':str(i),'source_reference':source,'known_at':known} for i in range(8)]
        self.ext={'schema_version':'DOCKTIMIZE_FLOW_EXTENSION_INPUT_V1','data_origin':'SYNTHETIC_FIXTURE','operational_approval':False,'timezone':'Asia/Seoul','interval_hours':6,'source_reference':source,'base_flow_sha256':sha,'known_at':known,'extension_start_at':a.isoformat(),'extension_end_at':(a+timedelta(days=2)).isoformat(),'tank_count':1,'record_count':8,'generation_method':'EXACT_14_DAY_REPETITION_PER_TANK_FROM_EXISTING_SYNTHETIC_FLOW','segments':extra};self.path=self.root/'extension.json';self.write()
    def write(self):self.path.write_text(json.dumps(self.ext))
    def build(self):
        with patch('src.daily_planning.integrated_input.flow_extension.validate',return_value={'data_preflight_pass':True}):return build_extended_snapshot(self.snapshot,base_flow_path=self.base,extension_path=self.path)
    def test_extension_is_separate_and_pattern_checked(self):
        before=copy.deepcopy(self.snapshot);base_bytes=self.base.read_bytes();ext_bytes=self.path.read_bytes();r=self.build()
        self.assertEqual(len(r['fixed_process_flows']['segments']),64);self.assertEqual(self.snapshot,before);self.assertEqual(self.base.read_bytes(),base_bytes);self.assertEqual(self.path.read_bytes(),ext_bytes)
        with patch('src.daily_planning.integrated_input.flow_extension.PROFILE',{'segments':56}),patch('src.daily_planning.integrated_input.flow_extension.validate',return_value={'data_preflight_pass':True}):self.assertTrue(validate_extended_snapshot(r)['data_preflight_pass'])
    def test_wrong_pattern_rejected(self):
        self.ext['segments'][0]['fixed_net_flow_bbl_per_hour']='999';self.write()
        with self.assertRaisesRegex(ValueError,'pattern mismatch'):self.build()
    def test_gap_and_duplicate_rejected(self):
        self.ext['segments'][1]=copy.deepcopy(self.ext['segments'][0]);self.write()
        with self.assertRaisesRegex(ValueError,'gap or overlap'):self.build()
    def test_per_segment_known_at_required(self):
        self.ext['segments'][0]['known_at']='2026-11-11T00:00:00+09:00';self.write()
        with self.assertRaisesRegex(ValueError,'Invalid extension segment'):self.build()
    def test_known_at_gate_only_when_extension_is_used(self):
        r=self.build();check_extension_available(r,as_of_at='2026-11-03T00:00:00+09:00',projection_end_at='2026-12-31T00:00:00+09:00')
        with self.assertRaisesRegex(ValueError,'FUTURE_INFORMATION_LEAKAGE'):check_extension_available(r,as_of_at='2026-11-03T00:00:00+09:00',projection_end_at='2027-01-01T00:00:00+09:00')
        check_extension_available(r,as_of_at='2026-12-30T00:00:00+09:00',projection_end_at='2027-01-02T00:00:00+09:00')
    def test_tank_projection_rejects_future_extension(self):
        from src.daily_planning.tank.v3_inventory import validate_v3_inventory
        snapshot=self.build()
        r=validate_v3_inventory(snapshot,[],start_at='2026-11-03T00:00:00+09:00',end_at='2027-01-01T00:00:00+09:00')
        self.assertEqual(r['status'],'INVALID_INPUT');self.assertIn('FUTURE_INFORMATION_LEAKAGE',r['issues'][0]['reason'])
    def test_extension_provenance_tampering_rejected(self):
        r=self.build();r['fixed_process_flows']['segments'][-1]['fixed_net_flow_bbl_per_hour']='999'
        with patch('src.daily_planning.integrated_input.flow_extension.PROFILE',{'segments':56}),patch('src.daily_planning.integrated_input.flow_extension.validate',return_value={'data_preflight_pass':True}):
            with self.assertRaisesRegex(ValueError,'content or known_at changed'):validate_extended_snapshot(r)
