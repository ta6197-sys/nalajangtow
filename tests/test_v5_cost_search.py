import copy
import unittest
from unittest.mock import patch
from tests.test_v5_physical_integration import synthetic,START
from src.daily_planning.integrated_v3.v5_settlement import settle_v5_call,price_v5_schedule,clock_audit
from src.daily_planning.integrated_v3.v5_cost_search import optimize_v5
from src.daily_planning.physical_scheduling.phases import replay


def fixture():
    s,p=synthetic();c=s['calls'][0];c.update(vessel_id='SYN-V',nor_tender_at=START,nor_valid='Y',notice_hours='0',allowed_hours='1',daily_demurrage_usd='1440',day_mode='SHINC',weather_policy='EXCLUDE',post_demurrage_exceptions='NONE',early_start_if_work='N',contract_holidays=[])
    source={'case_id':c['call_id'],'vessel_id':'SYN-V','quantity_mt':c['cargo_quantity_mt'],'laytime_basis':'FIXED_HOURS','allowance_value':'1','nor_tender_local':START,'nor_valid':'Y','notice_hours':'0','day_mode':'SHINC','weather_policy':'EXCLUDE','post_demurrage_exceptions':'NONE','early_start_if_work':'N','demurrage_usd_day':'1440'}
    s['workbook_tables']['contracts']={'rows':[source]};s['source_input']={'data_origin':'SYNTHETIC_FIXTURE','operational_approval':False,'planning_policy':{'laycan_exception':'SCENARIOS_ONLY_NOT_ACTIVE_IN_SETTLEMENT'}}
    return s,p


def record(row):
    a,b=row['work_intervals'][0]
    return {'call_id':row['call_id'],'work_start_at':row['work_start_at'],'finish_at':row['work_end_at'],'known_at':START,'source_reference':'ISOLATED_SYNTHETIC_EXPLICIT_EVENTS','coverage_attested':True,'event_intervals':[(START,a,'WAIT'),(a,b,'WORK')],'despatch_policy':'NONE','despatch_policy_reference':'SYNTHETIC_GROSS_ONLY','settlement_policy':'MINUTE_V1'}

class V5SettlementTest(unittest.TestCase):
    def setUp(self):
        self.s,self.p=fixture();self.c=self.s['calls'][0];self.row=replay(self.c,self.s['berths'][0],'2026-11-03T09:00:00+09:00',self.p);self.ev=record(self.row)
    def settle(self):return settle_v5_call(self.s,self.row,as_of_at=START,evidence=self.ev)
    def test_exact_contract_mapping_and_money(self):
        before=copy.deepcopy(self.s);r=self.settle();self.assertEqual(r['status'],'SETTLED',r);self.assertEqual(r['demurrage_cents'],54100);self.assertEqual(self.s,before)
    def test_identity_mismatch(self):
        self.s['workbook_tables']['contracts']['rows'][0]['vessel_id']='BAD';self.assertEqual(self.settle()['issues'][0]['code'],'CONTRACT_REFERENCE_UNRESOLVED')
    def test_missing_event_evidence_is_not_zero(self):
        r=settle_v5_call(self.s,self.row,as_of_at=START);self.assertEqual(r['status'],'SETTLEMENT_UNRESOLVED');self.assertNotIn('demurrage_cents',r)
    def test_invented_work_is_rejected(self):
        self.ev['event_intervals']=[(START,self.row['work_end_at'],'WAIT')];self.assertEqual(self.settle()['issues'][0]['code'],'CANDIDATE_EVENT_MISMATCH')
    def test_nor_notice_changes_clock_and_zero(self):
        self.c['notice_hours']='12';self.s['workbook_tables']['contracts']['rows'][0]['notice_hours']='12';r=self.settle();self.assertEqual(r['status'],'SETTLED',r);self.assertEqual(r['demurrage_cents'],0)
    def test_minute_and_second_precision_boundary(self):
        self.row['work_end_at']='2026-11-03T10:01:01+09:00';self.row['work_intervals'][0][1]=self.row['work_end_at'];self.ev=record(self.row)
        self.assertEqual(self.settle()['status'],'SETTLEMENT_UNRESOLVED');self.ev['settlement_policy']='SECOND_PRECISION_SIMULATION_V1';r=self.settle();self.assertEqual(r['status'],'SETTLED',r);self.assertTrue(r['simulation_only']);self.assertEqual(r['demurrage_cents'],54102)
    def test_unresolved_clock_audit_is_not_assumed_fixed(self):
        self.c['call_id']='DEM-0011';self.s['workbook_tables']['contracts']['rows'][0]['case_id']='DEM-0011';self.row['call_id']='DEM-0011';self.ev['call_id']='DEM-0011'
        self.assertEqual(self.settle()['issues'][0]['code'],'CLOCK_START_AUDIT_PENDING')
        r=settle_v5_call(self.s,self.row,as_of_at=START,evidence=self.ev,clock_policy='SIMULATION_DERIVED_CONTRACT_TERMS');self.assertEqual(r['status'],'SETTLED',r);self.assertTrue(r['simulation_only'])
    def test_weather_exceptions_use_ledger(self):
        self.ev['event_intervals']=[(START,'2026-11-03T00:30:00+09:00','WEATHER'),('2026-11-03T00:30:00+09:00',self.row['work_start_at'],'WAIT'),(self.row['work_start_at'],self.row['work_end_at'],'WORK')]
        r=self.settle();self.assertEqual(r['status'],'SETTLED',r);self.assertEqual(r['demurrage_cents'],51100)
    def test_holiday_exclusion_uses_contract_rules(self):
        self.c.update(day_mode='SHEX_WEEKEND',contract_holidays=['2026-11-03']);self.s['workbook_tables']['contracts']['rows'][0]['day_mode']='SHEX_WEEKEND';r=self.settle();self.assertEqual(r['status'],'SETTLED',r);self.assertEqual(r['demurrage_cents'],0)
    def test_post_demurrage_owner_exception(self):
        self.c['post_demurrage_exceptions']='OWNER_ONLY';self.s['workbook_tables']['contracts']['rows'][0]['post_demurrage_exceptions']='OWNER_ONLY';self.ev['event_intervals']=[(START,'2026-11-03T02:00:00+09:00','WAIT'),('2026-11-03T02:00:00+09:00','2026-11-03T03:00:00+09:00','OWNER_BREAKDOWN'),('2026-11-03T03:00:00+09:00',self.row['work_start_at'],'WAIT'),(self.row['work_start_at'],self.row['work_end_at'],'WORK')];r=self.settle();self.assertEqual(r['status'],'SETTLED',r);self.assertEqual(r['demurrage_cents'],48100)
    def test_clear_weather_cannot_override_source_weather_restriction(self):
        self.s['workbook_tables']['restrictions']['rows']=[{'berth_id':self.row['berth_id'],'reason_code':'WEATHER_DETERIORATION','cargo_group':None,'start_local':'2026-11-03T07:00:00+09:00','end_local':'2026-11-03T08:00:00+09:00'}]
        r=settle_v5_call(self.s,self.row,as_of_at=START,pricing_policy='CLEAR_WEATHER_BASELINE_V1');self.assertEqual(r['issues'][0]['code'],'SIMULATION_CONTRADICTS_SOURCE_WEATHER')
    def test_audit_real_v5_source_is_distinct(self):
        import json
        from pathlib import Path
        path=Path('results/v5_preflight/2207668409df0d34731476c5/normalized_snapshot.json')
        if not path.is_file():self.skipTest('V5 snapshot unavailable')
        a=clock_audit(json.loads(path.read_text()));self.assertEqual(len(a['historical_divergence_ids_in_v5']),40);self.assertNotEqual(a['audit_source_workbook_sha256'],a['v5_workbook_sha256']);self.assertFalse(a['v5_effective_start_available'])

class V5CostSearchTest(unittest.TestCase):
    def setUp(self):
        self.s,self.p=fixture();self.cid=self.s['calls'][0]['call_id'];self.etbs={self.cid:['2026-11-03T09:00:00+09:00','2026-11-03T12:00:00+09:00']};self.rows=[replay(self.s['calls'][0],self.s['berths'][0],t,self.p) for t in self.etbs[self.cid]];self.records=[record(r) for r in self.rows]
    def run_search(self,**kw):
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *a:copy.deepcopy(self.s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}),patch('src.daily_planning.integrated_v3.v5_physical.prepare_pilotage_policy',side_effect=lambda s,c,e,p,*a:(p,None)):
            return optimize_v5(self.s,data_dir='ISOLATED_SYNTHETIC_TEST',call_ids=[self.cid],as_of_at=START,policy=self.p,pilotage_context={},candidate_generation_mode='MANUAL_CANDIDATES',candidate_etbs=self.etbs,settlement_records=kw.pop('settlement_records',self.records),**kw)
    def test_two_valid_schedules_choose_lower_exact_engine_cost(self):
        r=self.run_search();self.assertEqual(r['optimization_status'],'PRICED_INCUMBENT',r);self.assertEqual(r['fully_priced_candidate_count'],2);self.assertEqual(sorted(x['total_demurrage_cents'] for x in r['evaluated_candidates']),[54100,72100]);self.assertEqual(r['best_total_demurrage_cents'],54100);self.assertEqual(r['best_candidate']['schedule'][0]['etb_at'],self.etbs[self.cid][0]);self.assertEqual({x['candidate_domain_fingerprint'] for x in r['evaluated_candidates']},{r['candidate_domain_fingerprint']});self.assertFalse(r['operational_approval'])
    def test_unresolved_not_cheaper_than_priced(self):
        r=self.run_search(settlement_records=[self.records[1]]);self.assertEqual(r['fully_priced_candidate_count'],1);self.assertEqual(r['unresolved_settlement_count'],1);self.assertEqual(r['best_total_demurrage_cents'],72100);self.assertIsNone(next(c for c in r['evaluated_candidates'] if c['settlement_validation']!='SETTLED')['total_demurrage_cents'])
    def test_no_evidence_no_incumbent(self):
        r=self.run_search(settlement_records=[]);self.assertEqual(r['optimization_status'],'SETTLEMENT_UNRESOLVED');self.assertIsNone(r['best_total_demurrage_cents'])
    def test_budget_stops_after_one(self):
        r=self.run_search(max_cost_candidates=1);self.assertEqual(r['evaluated_schedule_count'],1);self.assertEqual(r['stopping_reason'],'COST_CANDIDATE_LIMIT');self.assertEqual(r['optimality_scope'],'LOWEST_FULLY_PRICED_EVALUATED')
    def test_hard_fix_preserved(self):
        self.s['calls'][0].update(fixed_etb_is_hard=True,fix_etb_at=self.etbs[self.cid][0]);r=self.run_search();self.assertEqual(r['best_candidate']['schedule'][0]['etb_at'],self.etbs[self.cid][0]);self.assertEqual(r['fully_priced_candidate_count'],1)
    def test_inventory_invalid_is_not_priced(self):
        self.s['tanks'][0]['capacity_bbl']='550';r=self.run_search();self.assertEqual(r['fully_priced_candidate_count'],0);self.assertIsNone(r['best_candidate'])
    def test_auto_generated_frozen_domain_cost_search(self):
        times=['2026-11-03T00:01:00+09:00','2026-11-03T00:30:00+09:00'];records=[record(replay(self.s['calls'][0],self.s['berths'][0],t,self.p)) for t in times]
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',return_value=copy.deepcopy(self.s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}),patch('src.daily_planning.integrated_v3.v5_physical.prepare_pilotage_policy',side_effect=lambda s,c,e,p,*a:(p,None)):
            r=optimize_v5(self.s,data_dir='ISOLATED_SYNTHETIC_TEST',call_ids=[self.cid],as_of_at=START,policy=self.p,pilotage_context={},candidate_generation_mode='AUTO_ADAPTIVE_CANDIDATES',settlement_records=records)
        self.assertEqual(r['optimization_status'],'PRICED_INCUMBENT',r);self.assertEqual(r['fully_priced_candidate_count'],2);self.assertEqual(r['best_total_demurrage_cents'],200);self.assertEqual({c['candidate_domain_fingerprint'] for c in r['evaluated_candidates']},{r['candidate_domain_fingerprint']})
    def test_incumbent_survives_later_timeout(self):
        from src.daily_planning.integrated_v3.v5_cost_search import schedule_v5
        calls=[]
        def later(*args,**kwargs):
            calls.append(1)
            return schedule_v5(*args,**kwargs) if len(calls)==1 else {'status':'TIME_LIMIT_NO_INCUMBENT','stopping_reason':'TIME_BUDGET_EXHAUSTED'}
        with patch('src.daily_planning.integrated_v3.v5_cost_search.schedule_v5',side_effect=later):r=self.run_search()
        self.assertIsNotNone(r['best_candidate']);self.assertEqual(r['stopping_reason'],'TIME_BUDGET_EXHAUSTED')
    def test_incumbent_survives_later_solver_error(self):
        from src.daily_planning.integrated_v3.v5_cost_search import schedule_v5
        calls=[]
        def later(*args,**kwargs):
            calls.append(1)
            if len(calls)>1:raise RuntimeError('test solver failure')
            return schedule_v5(*args,**kwargs)
        with patch('src.daily_planning.integrated_v3.v5_cost_search.schedule_v5',side_effect=later):r=self.run_search()
        self.assertEqual(r['optimization_status'],'PRICED_INCUMBENT');self.assertIsNotNone(r['best_candidate']);self.assertIn('SEARCH_ERROR',r['stopping_reason'])

class V5RealDataUnresolvedTest(unittest.TestCase):
    def test_actual_v5_without_event_evidence_is_unresolved(self):
        from tests import test_v5_adaptive_candidates as adaptive
        from src.daily_planning.integrated_v3.v5_physical import schedule_v5
        captured=[]
        def capture(*args,**kwargs):
            r=schedule_v5(*args,**kwargs);captured.append((args,kwargs));return r
        with patch.object(adaptive,'schedule_v5',side_effect=capture):adaptive.RealAdaptiveTest('test_actual_subset_automatic').test_actual_subset_automatic()
        if not captured:self.skipTest('Actual V5 source unavailable')
        args,kw=captured[0];kw=dict(kw);kw['max_cost_candidates']=2;kw['settlement_records']=[]
        r=optimize_v5(*args,**kw)
        self.assertEqual(r['optimization_status'],'SETTLEMENT_UNRESOLVED',r);self.assertIsNone(r['best_total_demurrage_cents']);self.assertGreater(r['unresolved_settlement_count'],0)
        self.assertTrue(all(c['physical_validation']['status']=='VALID' and c['inventory_validation']=='INVENTORY_VALID' and c['total_demurrage_cents'] is None for c in r['evaluated_candidates']))
    def test_actual_v5_user_approved_clear_weather_simulation(self):
        from tests import test_v5_adaptive_candidates as adaptive
        from src.daily_planning.integrated_v3.v5_physical import schedule_v5
        captured=[]
        def capture(*args,**kwargs):
            r=schedule_v5(*args,**kwargs);captured.append((args,kwargs));return r
        with patch.object(adaptive,'schedule_v5',side_effect=capture):adaptive.RealAdaptiveTest('test_actual_subset_automatic').test_actual_subset_automatic()
        if not captured:self.skipTest('Actual V5 source unavailable')
        args,kw=captured[0];kw=dict(kw);kw.update(max_cost_candidates=2,settlement_records=[],pricing_policy='CLEAR_WEATHER_BASELINE_V1')
        r=optimize_v5(*args,**kw);self.assertEqual(r['optimization_status'],'PRICED_INCUMBENT',r);self.assertEqual(r['fully_priced_candidate_count'],2);self.assertTrue(all(c['physical_validation']['status']=='VALID' and c['inventory_validation']=='INVENTORY_VALID' for c in r['evaluated_candidates']));self.assertTrue(all(item['simulation_only'] for c in r['evaluated_candidates'] for item in c['vessel_demurrage_breakdown'].values()))
