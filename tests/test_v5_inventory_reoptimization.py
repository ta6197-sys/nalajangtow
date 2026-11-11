import copy
from datetime import timedelta
import time
import unittest
from unittest.mock import patch
from tests.test_v5_physical_integration import synthetic,START
from src.daily_planning.integrated_v3.v5_physical import schedule_v5
from src.daily_planning.integrated_v3.v5_reoptimization import search
from src.daily_planning.integrated_v3.coordinator import validate_v5_candidate
from src.daily_planning.physical_scheduling.phases import replay
from src.daily_planning.integrated_input.validator import timestamp

class RealSolverReoptimizationTest(unittest.TestCase):
    def setUp(self):self.s,self.p=synthetic();self.cid=self.s['calls'][0]['call_id']
    def run_schedule(self,times=None,**kw):
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *a:copy.deepcopy(self.s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}),patch('src.daily_planning.integrated_v3.v5_physical.prepare_pilotage_policy',side_effect=lambda s,c,e,p,*a:(p,None)):
            return schedule_v5(self.s,data_dir='ISOLATED_TEST',call_ids=[c['call_id'] for c in self.s['calls']],as_of_at=kw.pop('as_of_at',START),policy=self.p,candidate_etbs=times or {},**kw)
    def negative_external(self):
        self.s['tanks'][0].update(initial_inventory_bbl='100',min_inventory_bbl='50')
        self.s['fixed_process_flows']['segments']=[{'tank_id':'T','start_at':START,'end_at':'2026-11-03T04:00:00+09:00','fixed_net_flow_bbl_per_hour':'-20'},{'tank_id':'T','start_at':'2026-11-03T04:00:00+09:00','end_at':'2026-11-09T00:00:00+09:00','fixed_net_flow_bbl_per_hour':'0'}]
    def test_valid_first_candidate(self):
        r=self.run_schedule({self.cid:['2026-11-03T01:00:00+09:00']});self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID');self.assertEqual(len(r['reoptimization_attempts']),1);self.assertEqual(r['forbidden_schedule_count'],0)
    def test_invalid_then_valid_with_fixed_call_and_exact_nogood(self):
        bid=self.s['berths'][0]['berth_id'];self.p['berths'][bid].update(berthing_seconds=3600,hose_connection_seconds=7200,unberthing_seconds=3600)
        fixed=copy.deepcopy(self.s['calls'][0]);fixed.update(call_id='SYN-FIX',fixed_etb_is_hard=True,fix_etb_at='2026-11-03T20:00:00+09:00');self.s['calls'].append(fixed)
        self.s['tanks'][0].update(initial_inventory_bbl='100',min_inventory_bbl='40')
        self.s['fixed_process_flows']['segments']=[{'tank_id':'T','start_at':START,'end_at':'2026-11-03T14:00:00+09:00','fixed_net_flow_bbl_per_hour':'-5'},{'tank_id':'T','start_at':'2026-11-03T14:00:00+09:00','end_at':'2026-11-09T00:00:00+09:00','fixed_net_flow_bbl_per_hour':'0'}]
        window={'start_at':START,'end_at':'2026-11-09T00:00:00+09:00'};context={'daylight_path':'src/daily_planning/daylight_windows.csv','daylight_source':'Repository Ulsan daylight file','night_berthing_permissions':{bid:True},'departure_windows_by_berth_call':{bid:{self.cid:[window],'SYN-FIX':[window]}}}
        original=copy.deepcopy(self.s)
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',return_value=copy.deepcopy(self.s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}):
            r=schedule_v5(self.s,data_dir='ISOLATED_TEST',call_ids=[self.cid,'SYN-FIX'],as_of_at=START,policy=self.p,pilotage_context=context,candidate_etbs={self.cid:['2026-11-03T09:00:00+09:00','2026-11-03T12:00:00+09:00']})
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);attempts=r['reoptimization_attempts'];self.assertEqual(len(attempts),2);self.assertEqual(attempts[0]['physical_validation']['status'],'VALID');self.assertEqual(attempts[0]['inventory_validation_status'],'TANK_SUBPROBLEM_NOT_VALIDATED');self.assertEqual(attempts[1]['inventory_validation_status'],'INVENTORY_VALID');self.assertEqual(r['forbidden_schedule_count'],1)
        self.assertEqual(attempts[0]['selected_etbs']['SYN-FIX'],attempts[1]['selected_etbs']['SYN-FIX']);self.assertEqual(timestamp(attempts[1]['selected_etbs']['SYN-FIX']),timestamp(fixed['fix_etb_at']));self.assertNotEqual(attempts[0]['schedule_fingerprint'],attempts[1]['schedule_fingerprint']);self.assertTrue(any(i['tank_id']=='T' and i.get('at') and i.get('constraint')=='MINIMUM_INVENTORY' and self.cid in i.get('related_call_ids',[]) for i in attempts[0]['inventory_violations']));self.assertEqual(self.s,original)
    def test_all_candidates_invalid_domain_exhausted(self):
        self.negative_external();r=self.run_schedule({self.cid:['2026-11-03T04:00:00+09:00','2026-11-03T05:00:00+09:00']});self.assertEqual(r['status'],'INVENTORY_VALIDATION_FAILED');self.assertEqual(r['stopping_reason'],'CANDIDATE_DOMAIN_EXHAUSTED');self.assertEqual(len(r['reoptimization_attempts']),2);self.assertEqual(r['forbidden_schedule_count'],2)
    def test_reoptimization_limit_zero(self):
        self.negative_external();r=self.run_schedule({self.cid:['2026-11-03T04:00:00+09:00','2026-11-03T05:00:00+09:00']},max_inventory_reoptimizations=0);self.assertEqual(r['stopping_reason'],'REOPTIMIZATION_LIMIT_REACHED');self.assertEqual(len(r['reoptimization_attempts']),1)
    def test_capacity_violation(self):
        self.s['tanks'][0]['capacity_bbl']='550';r=self.run_schedule({self.cid:['2026-11-03T01:00:00+09:00']});self.assertTrue(any(i['code']=='INVENTORY_OUT_OF_BOUNDS' and i.get('constraint')=='MAXIMUM_CAPACITY' for i in r['inventory_violations']));self.assertNotEqual(r['combined_validation'],'PHYSICAL_AND_INVENTORY_VALID')
    def test_receipt_and_dispatch_limits(self):
        for direction,field,code in [('DISCHARGE','max_receipt_rate_bbl_per_hour','RECEIPT_RATE_EXCEEDED'),('LOAD','max_dispatch_rate_bbl_per_hour','DISPATCH_RATE_EXCEEDED')]:
            with self.subTest(direction=direction):
                self.s,self.p=synthetic();self.s['calls'][0]['direction']=direction;self.s['tanks'][0][field]='99';r=self.run_schedule({self.cid:['2026-11-03T01:00:00+09:00']});self.assertTrue(any(i['code']==code for i in r['inventory_violations']))
    def test_auto_mode_reuses_candidate_domain(self):
        r=self.run_schedule(candidate_generation_mode='AUTO_ADAPTIVE_CANDIDATES');self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID');self.assertGreater(r['final_candidate_count'],0)
    def test_auto_mode_invalid_then_feasible_same_domain(self):
        self.s['tanks'][0].update(initial_inventory_bbl='100',min_inventory_bbl='50')
        self.s['fixed_process_flows']['segments']=[{'tank_id':'T','start_at':START,'end_at':'2026-11-03T01:00:00+09:00','fixed_net_flow_bbl_per_hour':'-100'},{'tank_id':'T','start_at':'2026-11-03T01:00:00+09:00','end_at':'2026-11-09T00:00:00+09:00','fixed_net_flow_bbl_per_hour':'0'}]
        r=self.run_schedule(candidate_generation_mode='AUTO_ADAPTIVE_CANDIDATES');self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertEqual(len(r['reoptimization_attempts']),2);self.assertEqual(r['solver_attempt_count'],2);self.assertEqual(r['inventory_reoptimization_solver_calls'],1);self.assertEqual(r['forbidden_schedule_count'],1);self.assertEqual(r['reoptimization_attempts'][0]['inventory_validation_status'],'TANK_SUBPROBLEM_NOT_VALIDATED');self.assertEqual(r['reoptimization_attempts'][1]['inventory_validation_status'],'INVENTORY_VALID')
    def test_concurrent_receipt_and_dispatch(self):
        b=copy.deepcopy(self.s['berths'][0]);b['berth_id']='B2';self.s['berths'].append(b);self.s['tanks'][0]['connected_berth_ids'].append('B2');self.p['berths']['B2']=copy.deepcopy(self.p['berths'][self.s['berths'][0]['berth_id']]);c=copy.deepcopy(self.s['calls'][0]);c.update(call_id='SYN-B',direction='LOAD');self.s['calls'].append(c)
        t='2026-11-03T01:00:00+09:00';r=self.run_schedule({self.cid:[t],'SYN-B':[t]});self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);rates=r['final_inventory_result']['tank_result']['gross_vessel_rate_maxima']['T'];self.assertEqual(rates,{'receipt_bbl_per_hour':'100','dispatch_bbl_per_hour':'100'})
    def test_protected_transfer_and_carryover(self):
        row=replay(self.s['calls'][0],self.s['berths'][0],'2026-11-03T23:30:00+09:00',self.p)
        prior=validate_v5_candidate(self.s,[row],as_of_at=START,projection_end_at='2026-11-04T00:00:00+09:00');self.assertEqual(prior['status'],'INVENTORY_VALID',prior)
        state=prior['tank_result']['end_state'];r=self.run_schedule(as_of_at='2026-11-04T00:00:00+09:00',physical_commitments=[row],initial_inventory=state['inventory_bbl'],carried_transfers=state['carried_transfers']);self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertIn(row,r['reoptimization_attempts'][0]['physical_schedule'])
    def test_reoptimization_preserves_carried_protected_transfer(self):
        self.s['tanks'][0].update(initial_inventory_bbl='100',min_inventory_bbl='100')
        self.s['fixed_process_flows']['segments']=[{'tank_id':'T','start_at':START,'end_at':'2026-11-04T00:00:00+09:00','fixed_net_flow_bbl_per_hour':'0'},{'tank_id':'T','start_at':'2026-11-04T00:00:00+09:00','end_at':'2026-11-04T14:00:00+09:00','fixed_net_flow_bbl_per_hour':'-10'},{'tank_id':'T','start_at':'2026-11-04T14:00:00+09:00','end_at':'2026-11-09T00:00:00+09:00','fixed_net_flow_bbl_per_hour':'0'}]
        row=replay(self.s['calls'][0],self.s['berths'][0],'2026-11-03T23:30:00+09:00',self.p);prior=validate_v5_candidate(self.s,[row],as_of_at=START,projection_end_at='2026-11-04T00:00:00+09:00');state=prior['tank_result']['end_state'];c=copy.deepcopy(self.s['calls'][0]);c.update(call_id='SYN-B',eta_at='2026-11-04T00:00:00+09:00');self.s['calls'].append(c)
        r=self.run_schedule({'SYN-B':['2026-11-04T09:00:00+09:00','2026-11-04T12:00:00+09:00']},as_of_at='2026-11-04T00:00:00+09:00',physical_commitments=[row],initial_inventory=state['inventory_bbl'],carried_transfers=state['carried_transfers']);self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertEqual(len(r['reoptimization_attempts']),2);self.assertTrue(all(row in a['physical_schedule'] for a in r['reoptimization_attempts']));self.assertEqual(r['forbidden_schedule_count'],1)
    def test_budget_timeout_is_not_infeasibility(self):
        r=self.run_schedule({self.cid:['2026-11-03T01:00:00+09:00']},time_limit_seconds=1e-12);self.assertEqual(r['status'],'TIME_LIMIT_NO_INCUMBENT')

class ReoptimizationControlTest(unittest.TestCase):
    def payload(self,n):return {'status':'BERTH_SUBSYSTEM_FEASIBLE','solver_status':'OPTIMAL','schedule':[{'call_id':'C','berth_id':'B','etb_at':str(n)}]}
    def evaluate(self,rows):
        valid=rows[0]['etb_at']=='3';return {'status':'PHYSICAL_AND_INVENTORY_VALID' if valid else 'INVENTORY_VALIDATION_FAILED','physical_validation':{'status':'VALID'},'inventory_validation':'INVENTORY_VALID' if valid else 'TANK_SUBPROBLEM_NOT_VALIDATED','issues':[] if valid else [{'code':'INVENTORY_OUT_OF_BOUNDS','tank_id':'T','at':'2026-11-03T01:00:00+09:00'}]}
    def test_two_invalid_then_valid(self):
        calls=[]
        def next_(forbidden,remaining):calls.append(copy.deepcopy(forbidden));return self.payload(len(calls)+1)
        r=search(self.payload(1),evaluate=self.evaluate,solve_alternative=next_,deadline=time.perf_counter()+5,max_reoptimizations=3)
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID');self.assertEqual(len(r['attempts']),3);self.assertEqual([len(c) for c in calls],[1,2]);self.assertEqual(len(r['forbidden_schedules']),2)
    def test_duplicate_schedule_stops(self):
        r=search(self.payload(1),evaluate=self.evaluate,solve_alternative=lambda f,t:self.payload(1),deadline=time.perf_counter()+5,max_reoptimizations=3);self.assertEqual(r['stopping_reason'],'DUPLICATE_SCHEDULE_DETECTED');self.assertEqual(len(r['attempts']),1)
    def test_attempt_limit_is_retries_not_initial(self):
        calls=[]
        def next_(f,t):calls.append(1);return self.payload(2)
        r=search(self.payload(1),evaluate=self.evaluate,solve_alternative=next_,deadline=time.perf_counter()+5,max_reoptimizations=1);self.assertEqual(len(r['attempts']),2);self.assertEqual(r['stopping_reason'],'REOPTIMIZATION_LIMIT_REACHED');self.assertEqual(len(calls),1)
    def test_three_retries_after_initial(self):
        def invalid(_):return {'status':'INVENTORY_VALIDATION_FAILED','physical_validation':{'status':'VALID'},'inventory_validation':'TANK_SUBPROBLEM_NOT_VALIDATED','issues':[{'code':'INVENTORY_OUT_OF_BOUNDS'}]}
        def next_(forbidden,remaining):return self.payload(len(forbidden)+1)
        r=search(self.payload(1),evaluate=invalid,solve_alternative=next_,deadline=time.perf_counter()+5,max_reoptimizations=3);self.assertEqual(len(r['attempts']),4);self.assertEqual(r['solver_calls'],4);self.assertEqual(len(r['forbidden_schedules']),3);self.assertEqual(r['stopping_reason'],'REOPTIMIZATION_LIMIT_REACHED')
    def test_timeout_not_infeasibility(self):
        r=search(self.payload(1),evaluate=self.evaluate,solve_alternative=lambda f,t:{'status':'TIME_LIMIT_NO_INCUMBENT'},deadline=time.perf_counter()+5,max_reoptimizations=3);self.assertEqual(r['status'],'TIME_LIMIT_NO_INCUMBENT')
    def test_shared_budget_decreases(self):
        budgets=[]
        def next_(f,t):budgets.append(t);time.sleep(.005);return self.payload(len(budgets)+1)
        r=search(self.payload(1),evaluate=self.evaluate,solve_alternative=next_,deadline=time.perf_counter()+5,max_reoptimizations=3);self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID');self.assertGreater(budgets[0],budgets[1])
    def test_nonretryable_carryover_fails_closed(self):
        def invalid(_):return {'status':'INVENTORY_VALIDATION_FAILED','physical_validation':{'status':'VALID'},'issues':[{'code':'INVALID_CARRYOVER_STATE'}]}
        r=search(self.payload(1),evaluate=invalid,solve_alternative=lambda f,t:self.fail('must not retry'),deadline=time.perf_counter()+5,max_reoptimizations=3);self.assertEqual(r['stopping_reason'],'NON_RETRYABLE_INVENTORY_FAILURE')
