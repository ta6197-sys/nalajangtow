import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from datetime import timedelta
from src.daily_planning.integrated_v3.v5_physical import schedule_v5
from src.daily_planning.integrated_input.validator import timestamp
from tests.test_v3_physical_scheduling import scenario, START


def synthetic():
    s,p=scenario();c=s['calls'][0];b=s['berths'][0]
    c.update(cargo_group='X',direction='DISCHARGE',cargo_quantity_mt='10',cargo_volume_bbl='100',physical={'dwt_mt':'100','loa_m':'100','pbl_m':'70','draft_m':'5','grt':'50','bbl_per_mt':'10'},eta_at=START)
    b.update(active='Y',allowed_cargo_groups='X',allowed_operations='LOAD,DISCHARGE',max_dwt_mt='1000',min_loa_m='0',max_loa_m='200',min_pbl_m='10',max_draft_m='10',draft_check_operation='BOTH',max_transfer_rate_mt_h='10',connected_tank_ids='T')
    s.update(schema_version='DOCKTIMIZE_NORMALIZED_INPUT_V5',scenario_id='V5_3TANK',source_input={})
    s['tanks']=[{'tank_id':'T','cargo_group':'X','connected_berth_ids':[b['berth_id']],'capacity_bbl':'1000','min_inventory_bbl':'0','initial_inventory_bbl':'500','max_receipt_rate_bbl_per_hour':'100','max_dispatch_rate_bbl_per_hour':'100'}]
    s['fixed_process_flows']={'inventory_as_of_at':START,'segments':[{'tank_id':'T','start_at':START,'end_at':'2026-11-09T00:00:00+09:00','fixed_net_flow_bbl_per_hour':'0'}]}
    p['cargo_duration_policy']='VESSEL_BERTH_RATE_V1'
    return s,p

class V5PhysicalSyntheticTest(unittest.TestCase):
    def setUp(self):self.s,self.p=synthetic();self.cid=self.s['calls'][0]['call_id'];self.bid=self.s['berths'][0]['berth_id'];self.times={self.cid:['2026-11-03T02:00:00+09:00']}
    def run_case(self):
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *args:copy.deepcopy(self.s)), patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}), patch('src.daily_planning.integrated_v3.v5_physical.prepare_pilotage_policy',side_effect=lambda s,c,e,p,*args:(p,None)):
            return schedule_v5(self.s,data_dir='SYNTHETIC_TEST',call_ids=[c['call_id'] for c in self.s['calls']],as_of_at=START,candidate_etbs=self.times,policy=self.p)
    def test_one_vessel_exact_phases_and_inventory(self):
        before=copy.deepcopy((self.s,self.p,self.times));r=self.run_case();self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertEqual(r['physical_validation']['status'],'VALID');self.assertEqual(r['inventory_validation'],'INVENTORY_VALID');self.assertTrue(r['inventory_result']['tank_result']['quantity_reconciliation'][self.cid]['matched']);self.assertEqual(r['selected_berths'][self.cid],self.bid);self.assertEqual((self.s,self.p,self.times),before)
    def test_ordinary_etb_not_before_eta(self):
        self.times[self.cid]=['2026-11-02T23:00:00+09:00','2026-11-03T02:00:00+09:00'];r=self.run_case();self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID');self.assertGreaterEqual(timestamp(r['selected_etbs'][self.cid]),timestamp(self.s['calls'][0]['eta_at']))
    def test_hard_fix_exact(self):
        self.s['calls'][0].update(fixed_etb_is_hard=True,fix_etb_at='2026-11-03T03:00:00+09:00');r=self.run_case();self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertEqual(timestamp(r['selected_etbs'][self.cid]),timestamp(self.s['calls'][0]['fix_etb_at']))
    def test_multiple_berths_and_simultaneous_transfers(self):
        b=copy.deepcopy(self.s['berths'][0]);b['berth_id']='B2';self.s['berths'].append(b);self.s['tanks'][0]['connected_berth_ids'].append('B2');self.p['berths']['B2']=copy.deepcopy(self.p['berths'][self.bid]);c=copy.deepcopy(self.s['calls'][0]);c['call_id']='SYN-B';c['direction']='LOAD';self.s['calls'].append(c);self.times['SYN-B']=self.times[self.cid];r=self.run_case();self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertEqual(len(set(r['selected_berths'].values())),2)
    def test_missing_policy(self):
        self.p={};self.assertEqual(self.run_case()['status'],'UNSUPPORTED_OPERATIONAL_POLICY')
    def test_unresolved_4142(self):
        self.s['workbook_tables']['groups']['rows']=[{'rule_id':'DOCK-4142','rule_type':'PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP','berth_ids':self.bid+';B2'}];self.assertEqual(self.run_case()['status'],'UNSUPPORTED_OPERATIONAL_POLICY')
    def test_incompatible_dimensions(self):
        self.s['calls'][0]['physical']['dwt_mt']='2000';r=self.run_case();self.assertEqual(r['issues'][0]['code'],'NO_FEASIBLE_GENERATED_OPTION')
    def test_wrong_cargo(self):
        self.s['calls'][0]['cargo_group']='OTHER';r=self.run_case();self.assertEqual(r['issues'][0]['code'],'NO_FEASIBLE_GENERATED_OPTION')
    def test_wrong_operation(self):
        self.s['berths'][0]['allowed_operations']='LOAD';r=self.run_case();self.assertEqual(r['issues'][0]['code'],'NO_FEASIBLE_GENERATED_OPTION')
    def test_missing_connection(self):
        self.s['tanks'][0]['connected_berth_ids']=[];r=self.run_case();self.assertEqual(r['issues'][0]['code'],'NO_FEASIBLE_GENERATED_OPTION')
    def test_same_berth_conflict(self):
        c=copy.deepcopy(self.s['calls'][0]);c['call_id']='SYN-B';self.s['calls'].append(c);self.times['SYN-B']=self.times[self.cid];r=self.run_case();self.assertEqual(r['status'],'INFEASIBLE')
    def test_fixed_conflict(self):
        self.s['calls'][0].update(fixed_etb_is_hard=True,fix_etb_at='2026-11-03T03:00:00+09:00');c=copy.deepcopy(self.s['calls'][0]);c['call_id']='SYN-B';self.s['calls'].append(c);r=self.run_case();self.assertEqual(r['status'],'INFEASIBLE')
    def test_invalid_inventory(self):
        self.s['tanks'][0]['capacity_bbl']='550';r=self.run_case();self.assertEqual(r['status'],'INVENTORY_VALIDATION_FAILED',r);self.assertIn('INVENTORY_OUT_OF_BOUNDS',[x['code'] for x in r['issues']]);self.assertEqual(r['physical_validation']['status'],'VALID')
    def test_inventory_minimum(self):
        self.s['calls'][0]['direction']='LOAD';self.s['tanks'][0]['min_inventory_bbl']='450';r=self.run_case();self.assertEqual(r['status'],'INVENTORY_VALIDATION_FAILED');self.assertIn('INVENTORY_OUT_OF_BOUNDS',[x['code'] for x in r['issues']])
    def test_throughput_limit(self):
        self.s['tanks'][0]['max_receipt_rate_bbl_per_hour']='99';r=self.run_case();self.assertEqual(r['status'],'INVENTORY_VALIDATION_FAILED');self.assertIn('RECEIPT_RATE_EXCEEDED',[x['code'] for x in r['issues']])
    def test_dispatch_limit(self):
        self.s['calls'][0]['direction']='LOAD';self.s['tanks'][0]['max_dispatch_rate_bbl_per_hour']='99';r=self.run_case();self.assertEqual(r['status'],'INVENTORY_VALIDATION_FAILED');self.assertIn('DISPATCH_RATE_EXCEEDED',[x['code'] for x in r['issues']])
    def test_no_option_in_supplied_domain(self):
        self.times[self.cid]=[START];r=self.run_case();self.assertEqual(r['issues'][0]['code'],'NO_FEASIBLE_GENERATED_OPTION');self.assertIn('supplied candidate',r['physical_solver_result']['scope'])
    def test_time_limit_not_infeasibility(self):
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',return_value=copy.deepcopy(self.s)),patch('src.daily_planning.integrated_v3.v5_physical.prepare_pilotage_policy',side_effect=lambda s,c,e,p,*args:(p,None)),patch('src.daily_planning.integrated_v3.v5_physical.solve_berth_schedule',return_value={'status':'TIME_LIMIT_NO_INCUMBENT','issues':[{'code':'TIME_BUDGET_EXHAUSTED'}]}):
            r=schedule_v5(self.s,data_dir='SYNTHETIC_TEST',call_ids=[self.cid],as_of_at=START,candidate_etbs=self.times,policy=self.p)
        self.assertEqual(r['status'],'TIME_LIMIT_NO_INCUMBENT');self.assertEqual(r['combined_validation'],'NOT_VALIDATED')
    def test_real_tiny_solver_budget_expires_without_feasibility_claim(self):
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',return_value=copy.deepcopy(self.s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}), patch('src.daily_planning.integrated_v3.v5_physical.prepare_pilotage_policy',side_effect=lambda s,c,e,p,*args:(p,None)):
            r=schedule_v5(self.s,data_dir='SYNTHETIC_TEST',call_ids=[self.cid],as_of_at=START,candidate_etbs=self.times,policy=self.p,time_limit_seconds=1e-12)
        self.assertEqual(r['status'],'TIME_LIMIT_NO_INCUMBENT');self.assertEqual(r['combined_validation'],'NOT_VALIDATED')

class V5InputBoundaryTest(unittest.TestCase):
    def test_altered_real_snapshot_is_rejected(self):
        path=Path('results/v5_preflight/2207668409df0d34731476c5/normalized_snapshot.json');data=Path('../data/Docktimize_v5_input')
        if not path.exists() or not data.is_dir():self.skipTest('Supplied v5 snapshot or data unavailable')
        snapshot=json.loads(path.read_text());snapshot['calls'][0]['cargo_volume_bbl']='1'
        r=schedule_v5(snapshot,data_dir=data,call_ids=[snapshot['calls'][0]['call_id']],as_of_at=START,candidate_etbs={},policy={})
        self.assertEqual(r['issues'][0]['code'],'INVALID_V5_INPUT');self.assertEqual(r['solver_status'],'NOT_RUN')
    def test_inaccessible_data_fails_closed(self):
        s,_=synthetic();r=schedule_v5(s,data_dir='/nonexistent/v5-source',call_ids=['SYN-A'],as_of_at=START,candidate_etbs={},policy={})
        self.assertEqual(r['issues'][0]['code'],'INVALID_V5_INPUT')

class V5PilotagePolicyTest(unittest.TestCase):
    def setUp(self):
        from src.daily_planning.integrated_v3.v5_pilotage import prepare_pilotage_policy
        self.prepare=prepare_pilotage_policy;self.s,self.p=synthetic();self.cid=self.s['calls'][0]['call_id'];self.bid=self.s['berths'][0]['berth_id'];self.eligible={self.cid:[self.bid]}
        self.p['berths'][self.bid].update(berthing_seconds=3600,hose_connection_seconds=7200,unberthing_seconds=3600)
        self.context={'daylight_path':'src/daily_planning/daylight_windows.csv','daylight_source':'Repository daylight file test','night_berthing_permissions':{self.bid:True},'departure_windows_by_berth_call':{self.bid:{self.cid:[{'start_at':START,'end_at':'2026-11-09T00:00:00+09:00'}]}}}
    def apply(self,at='2026-11-03T10:00:00+09:00'):
        return self.prepare(self.s,[self.cid],self.eligible,self.p,self.context,START,{self.cid:[at]})
    def test_30k_grt_last_pob_sunset_minus_90(self):
        self.s['calls'][0]['physical']['grt']='30000';policy,issue=self.apply();self.assertIsNone(issue);w=policy['berths'][self.bid]['pilotage_windows_by_call'][self.cid]['berthing'][0];self.assertTrue(w['start_at'].endswith('06:46:00+09:00'));self.assertTrue(w['latest_start_at'].endswith('15:56:00+09:00'))
    def test_50k_grt_last_pob_sunset_minus_120(self):
        self.s['calls'][0]['physical']['grt']='50000';policy,issue=self.apply();self.assertIsNone(issue);w=policy['berths'][self.bid]['pilotage_windows_by_call'][self.cid]['berthing'][0];self.assertTrue(w['latest_start_at'].endswith('15:26:00+09:00'))
    def test_night_requires_berth_permission(self):
        del self.context['night_berthing_permissions'][self.bid];policy,issue=self.apply();self.assertIsNone(policy);self.assertIn('night',issue['reason'])
    def test_unberthing_is_separate_and_required(self):
        del self.context['departure_windows_by_berth_call'][self.bid][self.cid];policy,issue=self.apply();self.assertIsNone(policy);self.assertIn('unberthing',issue['reason'])
    def test_low_grt_deep_draft_is_pending(self):
        self.s['calls'][0]['physical']['draft_m']='10.5';policy,issue=self.apply();self.assertIsNone(policy);self.assertIn('draft',issue['reason'])
    def test_unresolved_0200_0800_rule_is_reported(self):
        policy,issue=self.apply('2026-11-03T07:00:00+09:00');self.assertIsNone(policy);self.assertIn('02:00-08:00',issue['reason'])
    def test_no_real_daylight_source_is_pending(self):
        self.context['daylight_path']='/nonexistent/daylight.csv';policy,issue=self.apply();self.assertIsNone(policy);self.assertEqual(issue['code'],'UNSUPPORTED_OPERATIONAL_POLICY')

class V5RealIntegrationTest(unittest.TestCase):
    def test_real_v5_fixed_and_ordinary_pipeline(self):
        from src.daily_planning.integrated_input.provenance import digest,canonical
        import hashlib
        path=Path('results/v5_preflight/2207668409df0d34731476c5/normalized_snapshot.json');data=Path('../data/Docktimize_v5_input');day=Path('src/daily_planning/daylight_windows.csv')
        if not path.is_file() or not data.is_dir() or not day.is_file():self.skipTest('V5 snapshot, source data or official daylight file unavailable')
        s=json.loads(path.read_text());before=digest(canonical(s).encode());source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in data.iterdir() if p.is_file()}
        calls=['DEM-0103','DEM-0112'];window={'start_at':START,'end_at':'2026-11-09T00:00:00+09:00'}
        policy={'schema_version':'V3_SYNTHETIC_PHASE_POLICY_V1','data_origin':'SYNTHETIC_FIXTURE','operational_approval':False,'source_reference':'User-specified V5 test policy: 60/120/60, 90 tugs, 1 per maneuver; not operational approval','cargo_duration_policy':'VESSEL_BERTH_RATE_V1','tug_capacity':90,'dock_4142_version':'DISABLED','berths':{}}
        permissions={};departures={}
        for b in s['berths']:
            bid=b['berth_id'];policy['berths'][bid]={'berthing_seconds':3600,'hose_connection_seconds':7200,'unberthing_seconds':3600,'pilot_boarding_to_berthing_seconds':0,'pilot_boarding_to_unberthing_seconds':0,'tug_units':1,'pilotage_windows':[dict(window)],'work_windows':[dict(window)]};permissions[bid]=True
            departures[bid]={cid:[dict(window)] for cid in calls}
        context={'daylight_path':str(day),'daylight_source':'Repository official daily Ulsan values: src/daily_planning/daylight_windows.csv','night_berthing_permissions':permissions,'departure_windows_by_berth_call':departures}
        r=schedule_v5(s,data_dir=data,call_ids=calls,as_of_at=START,candidate_etbs={'DEM-0112':['2026-11-04T10:00:00+09:00','2026-11-04T11:00:00+09:00']},policy=policy,pilotage_context=context,time_limit_seconds=20)
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertEqual(r['physical_validation']['status'],'VALID');self.assertEqual(r['inventory_validation'],'INVENTORY_VALID');self.assertEqual(set(r['scheduled_calls']),set(calls));self.assertEqual(timestamp(r['selected_etbs']['DEM-0103']),timestamp('2026-11-03T20:30+09:00'));self.assertTrue(all(x['matched'] for x in r['inventory_result']['tank_result']['quantity_reconciliation'].values()));self.assertEqual(before,digest(canonical(s).encode()));self.assertEqual(source_hashes,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in data.iterdir() if p.is_file()})
