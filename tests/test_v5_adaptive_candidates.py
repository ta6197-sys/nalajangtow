import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.test_v5_physical_integration import synthetic,START
from src.daily_planning.integrated_v3.v5_physical import schedule_v5
from src.daily_planning.integrated_v3.v5_candidates import CandidatePool
from src.daily_planning.integrated_input.validator import timestamp


class AdaptiveTest(unittest.TestCase):
    def setUp(self):self.s,self.p=synthetic();self.cid=self.s['calls'][0]['call_id'];self.bid=self.s['berths'][0]['berth_id']
    def run_auto(self,**kw):
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *a:copy.deepcopy(self.s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}),patch('src.daily_planning.integrated_v3.v5_physical.prepare_pilotage_policy',side_effect=lambda s,c,e,p,*a:(p,None)):
            return schedule_v5(self.s,data_dir='SYNTHETIC',call_ids=[c['call_id'] for c in self.s['calls']],as_of_at=START,policy=self.p,candidate_generation_mode='AUTO_ADAPTIVE_CANDIDATES',**kw)
    def test_automatic_generation(self):
        r=self.run_auto();self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertGreater(r['initial_candidate_count'],0);self.assertEqual(r['physical_validation']['status'],'VALID');self.assertEqual(r['inventory_validation'],'INVENTORY_VALID');self.assertEqual(r['candidate_generation_mode'],'AUTO_ADAPTIVE_CANDIDATES')
        self.assertGreaterEqual(timestamp(r['selected_etbs'][self.cid]),timestamp(self.s['calls'][0]['eta_at']))
    def test_determinism_and_reasons(self):
        a=self.run_auto();b=self.run_auto();self.assertEqual(a['candidates_by_call_and_berth'],b['candidates_by_call_and_berth']);self.assertEqual(a['generation_reasons'],b['generation_reasons']);v=a['generation_reasons'][self.cid][self.bid];self.assertTrue(any('ETA_AND_BERTHING_DURATION' in x['reasons'] for x in v));self.assertEqual(len(v),len({x['etb_at'] for x in v}))
    def test_hard_fix_immutable(self):
        self.s['calls'][0].update(fixed_etb_is_hard=True,fix_etb_at='2026-11-03T01:00:00+09:00');r=self.run_auto();self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertEqual(timestamp(r['selected_etbs'][self.cid]),timestamp(self.s['calls'][0]['fix_etb_at']))
    def test_berth_specific_candidates(self):
        b=copy.deepcopy(self.s['berths'][0]);b['berth_id']='B2';self.s['berths'].append(b);self.s['tanks'][0]['connected_berth_ids'].append('B2');self.p['berths']['B2']=copy.deepcopy(self.p['berths'][self.bid]);self.p['berths']['B2']['berthing_seconds']=120
        r=self.run_auto();domains=r['candidates_by_call_and_berth'][self.cid];self.assertNotEqual(domains[self.bid][0],domains['B2'][0])
    def test_expansion_after_conflict(self):
        c=copy.deepcopy(self.s['calls'][0]);c['call_id']='SYN-B';self.s['calls'].append(c);r=self.run_auto();self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertEqual(r['generation_rounds'][0]['solver_outcome'],'INFEASIBLE');self.assertGreater(r['expansion_events'][0]['added'],0);self.assertGreater(r['final_candidate_count'],r['initial_candidate_count'])
    def test_initial_shortage_can_expand(self):
        with patch.object(CandidatePool,'initial',return_value=None):r=self.run_auto()
        self.assertEqual(r['initial_candidate_count'],0);self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertGreater(r['expansion_events'][0]['added'],0)
    def test_finite_domain_exhaustion(self):
        c=copy.deepcopy(self.s['calls'][0]);c['call_id']='SYN-B';self.s['calls'].append(c);r=self.run_auto(max_expansion_rounds=0,max_candidates_per_pair=1);self.assertEqual(r['status'],'INFEASIBLE');self.assertEqual(r['stopping_reason'],'EXPANSION_ROUND_LIMIT');self.assertEqual(r['combined_validation'],'NOT_VALIDATED')
    def test_bounds_and_per_pair_limit(self):
        r=self.run_auto(max_candidates_per_pair=1,max_total_candidates=1);self.assertLessEqual(r['final_candidate_count'],1)
    def test_no_policy_does_not_prove_infeasibility(self):
        self.p={};r=self.run_auto();self.assertEqual(r['status'],'UNSUPPORTED_OPERATIONAL_POLICY')
    def test_no_compatible_berth_is_candidate_shortage(self):
        self.s['calls'][0]['cargo_group']='UNKNOWN';r=self.run_auto();self.assertEqual(r['status'],'NO_FEASIBLE_GENERATED_OPTION');self.assertEqual(r['issues'][0]['code'],'NO_FEASIBLE_GENERATED_OPTION')
    def test_candidate_specific_unresolved(self):
        self.s['calls'][0]['eta_at']='2026-11-03T03:00:00+09:00';r=self.run_auto(max_expansion_rounds=0);self.assertEqual(r['status'],'UNSUPPORTED_OPERATIONAL_POLICY');self.assertTrue(any(x.get('code')=='POLICY_UNRESOLVED' for x in r['rejection_reasons']))
    def test_unresolved_time_does_not_block_safe_time(self):
        self.s['calls'][0]['eta_at']='2026-11-03T03:00:00+09:00'
        self.p['berths'][self.bid]['pilotage_windows']=[{'start_at':'2026-11-03T03:00:00+09:00','end_at':'2026-11-03T04:00:00+09:00'},{'start_at':'2026-11-03T09:00:00+09:00','end_at':'2026-11-09T00:00:00+09:00'}]
        r=self.run_auto();self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertTrue(any(x['code']=='POLICY_UNRESOLVED' for x in r['rejection_reasons']));self.assertGreaterEqual(timestamp(r['selected_etbs'][self.cid]),timestamp('2026-11-03T09:00:00+09:00'))
    def test_inventory_failure_keeps_physical_schedule(self):
        self.s['tanks'][0]['capacity_bbl']='550';r=self.run_auto();self.assertEqual(r['status'],'INVENTORY_VALIDATION_FAILED');self.assertEqual(r['physical_validation']['status'],'VALID');self.assertIn('schedule',r['physical_solver_result']);self.assertTrue(any(x['code']=='INVENTORY_OUT_OF_BOUNDS' for x in r['issues']))
    def test_shared_budget_timeout(self):
        r=self.run_auto(time_limit_seconds=1e-12);self.assertEqual(r['status'],'TIME_LIMIT_NO_INCUMBENT');self.assertEqual(r['combined_validation'],'NOT_VALIDATED')
    def test_solver_budget_is_not_reset(self):
        budgets=[]
        def fail(**kw):budgets.append(kw['time_limit_seconds']);return {'status':'INFEASIBLE','issues':[]}
        def fake(*args,**kw):return fail(**kw)
        with patch('src.daily_planning.integrated_v3.v5_physical.solve_berth_schedule',side_effect=fake):r=self.run_auto(time_limit_seconds=10,max_expansion_rounds=2)
        self.assertGreaterEqual(len(budgets),2);self.assertTrue(all(a>b for a,b in zip(budgets,budgets[1:])));self.assertNotEqual(r['combined_validation'],'PHYSICAL_AND_INVENTORY_VALID')
    def test_unresolved_4142_does_not_erase_safe_berth(self):
        b=copy.deepcopy(self.s['berths'][0]);b['berth_id']='B2';self.s['berths'].append(b);self.s['tanks'][0]['connected_berth_ids'].append('B2');self.p['berths']['B2']=copy.deepcopy(self.p['berths'][self.bid])
        self.s['workbook_tables']['groups']['rows']=[{'rule_id':'DOCK-4142','rule_type':'PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP','berth_ids':self.bid+';B3'}]
        r=self.run_auto();self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertEqual(r['selected_berths'][self.cid],'B2');self.assertTrue(any(x['berth_id']==self.bid and x['code']=='POLICY_UNRESOLVED' for x in r['rejection_reasons']))
    def test_protected_berth_release_is_used(self):
        from src.daily_planning.physical_scheduling.phases import replay
        c=copy.deepcopy(self.s['calls'][0]);c['call_id']='SYN-B';self.s['calls'].append(c)
        fixed=replay(self.s['calls'][0],self.s['berths'][0],'2026-11-03T00:01:00+09:00',self.p)
        r=self.run_auto(physical_commitments=[fixed]);self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertIn(fixed,r['physical_solver_result']['schedule']);self.assertTrue(any('PROTECTED_BERTH_CLEARANCE' in x['reasons'] for x in r['generation_reasons']['SYN-B'][self.bid]))
    def test_existing_manual_mode(self):
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',return_value=copy.deepcopy(self.s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}),patch('src.daily_planning.integrated_v3.v5_physical.prepare_pilotage_policy',side_effect=lambda s,c,e,p,*a:(p,None)):
            r=schedule_v5(self.s,data_dir='SYNTHETIC',call_ids=[self.cid],as_of_at=START,policy=self.p,candidate_etbs={self.cid:['2026-11-03T01:00:00+09:00']})
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID');self.assertEqual(r['candidate_generation_mode'],'MANUAL_CANDIDATES')
    def test_restriction_boundary_and_invalid_pilotage(self):
        self.s['workbook_tables']['restrictions']['rows']=[{'berth_id':self.bid,'start_local':'2026-11-03T00:00:00+09:00','end_local':'2026-11-03T01:00:00+09:00','restriction_scope':'BOTH','cargo_group':None}]
        r=self.run_auto();self.assertTrue(any('BERTH_RESTRICTION_END' in x['reasons'] for x in r['generation_reasons'][self.cid][self.bid]));self.assertTrue(r['rejection_reasons'])

class RealAdaptiveTest(unittest.TestCase):
    def test_actual_subset_automatic(self):
        path=Path('results/v5_preflight/2207668409df0d34731476c5/normalized_snapshot.json');data=Path('../data/Docktimize_v5_input');day=Path('src/daily_planning/daylight_windows.csv')
        if not path.is_file() or not data.is_dir() or not day.is_file():self.skipTest('Actual V5 input or daylight missing')
        s=json.loads(path.read_text());calls=['DEM-0103','DEM-0112'];window={'start_at':START,'end_at':'2026-11-09T00:00:00+09:00'}
        policy={'schema_version':'V3_SYNTHETIC_PHASE_POLICY_V1','data_origin':'SYNTHETIC_FIXTURE','operational_approval':False,'source_reference':'User-specified V5 test policy: 60/120/60; 90 tugs; one per maneuver','cargo_duration_policy':'VESSEL_BERTH_RATE_V1','tug_capacity':90,'dock_4142_version':'DISABLED','berths':{}}
        permissions={};departures={}
        for b in s['berths']:
            bid=b['berth_id'];policy['berths'][bid]={'berthing_seconds':3600,'hose_connection_seconds':7200,'unberthing_seconds':3600,'pilot_boarding_to_berthing_seconds':0,'pilot_boarding_to_unberthing_seconds':0,'tug_units':1,'pilotage_windows':[dict(window)],'work_windows':[dict(window)]};permissions[bid]=True;departures[bid]={cid:[dict(window)] for cid in calls}
        context={'daylight_path':str(day),'daylight_source':'Repository official Ulsan daylight values','night_berthing_permissions':permissions,'departure_windows_by_berth_call':departures}
        r=schedule_v5(s,data_dir=data,call_ids=calls,as_of_at=START,policy=policy,pilotage_context=context,candidate_generation_mode='AUTO_ADAPTIVE_CANDIDATES',time_limit_seconds=20)
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertEqual(timestamp(r['selected_etbs']['DEM-0103']),timestamp('2026-11-03T20:30+09:00'));self.assertEqual(r['inventory_validation'],'INVENTORY_VALID')
