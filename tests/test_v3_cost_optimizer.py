import copy
import unittest
from unittest.mock import patch
from src.daily_planning.integrated_v3.optimizer import optimize_72h

class CostOptimizerTest(unittest.TestCase):
    def setUp(self):
        from tests.test_v3_integration_checkpoint import IntegrationCheckpointTest
        f=IntegrationCheckpointTest();f.setUp();self.s=f.s;self.p=f.p
        c=self.s['calls'][0];b=self.s['berths'][0];b['max_transfer_rate_mt_h']='1';c.update(contract_id='SYN-A',vessel_id='SYN-V',nor_tender_at='2026-11-03T00:00:00+09:00',nor_valid='Y',notice_hours='0',allowed_hours='0',workbook_allowed_hours_exact='0',daily_demurrage_usd='1440',day_mode='SHINC',weather_policy='EXCLUDE',post_demurrage_exceptions='NONE',early_start_if_work='N',contract_holidays=[])
        source={'case_id':'SYN-A','vessel_id':'SYN-V','quantity_mt':'1','laytime_basis':'FIXED_HOURS','allowance_value':'0','nor_tender_local':c['nor_tender_at'],'nor_valid':'Y','notice_hours':'0','day_mode':'SHINC','weather_policy':'EXCLUDE','post_demurrage_exceptions':'NONE','early_start_if_work':'N','demurrage_usd_day':'1440'}
        self.s['workbook_tables']['contracts']={'rows':[source]};self.s['source_input']={'data_origin':'SYNTHETIC_FIXTURE','operational_approval':False,'planning_policy':{'laycan_exception':'SCENARIOS_ONLY_NOT_ACTIVE_IN_SETTLEMENT'}}
        self.etbs={'SYN-A':['2026-11-03T01:00:00+09:00','2026-11-03T02:00:00+09:00']};self.records=[]
        for hour in (1,2):
            start=f'2026-11-03T{hour:02d}:01:00+09:00';end=f'2026-11-03T{hour+1:02d}:01:00+09:00'
            self.records.append({'call_id':'SYN-A','work_start_at':start,'finish_at':end,'known_at':'2026-11-03T00:00:00+09:00','source_reference':'TEST_ONLY','coverage_attested':True,'event_intervals':[('2026-11-03T00:00:00+09:00',end,'WAIT')],'despatch_policy':'NONE','despatch_policy_reference':'TEST_ONLY','settlement_policy':'SECOND_PRECISION_SIMULATION_V1'})
    def run_optimizer(self,**kwargs):
        with patch('src.daily_planning.integrated_input.validator.validate',return_value={'data_preflight_pass':True}):
            return optimize_72h(self.s,as_of_at='2026-11-03T00:00:00+09:00',policy=self.p,candidate_etbs=self.etbs,settlement_records=self.records,time_limit_seconds=15,**kwargs)
    def test_search_compares_exact_cost_and_preserves_inputs(self):
        before=copy.deepcopy((self.s,self.p,self.records));r=self.run_optimizer();self.assertEqual(r['status'],'SIMULATION_FEASIBLE',r);self.assertTrue(r['domain_exhausted']);self.assertTrue(r['best_among_enumerated']);self.assertEqual(r['incumbent']['total_demurrage_cents'],12100);self.assertEqual(r['incumbent']['berth_schedule'][0]['etb_at'],'2026-11-03T01:00:00+09:00');self.assertEqual(len(r['iterations']),2);self.assertEqual((self.s,self.p,self.records),before);self.assertFalse(r['operational_approval'])
    def test_missing_evidence_never_becomes_zero_cost(self):
        self.records=[];r=self.run_optimizer();self.assertEqual(r['status'],'UNRESOLVED_NO_VALIDATED_INCUMBENT',r);self.assertIsNone(r['incumbent'])
    def test_future_information_is_not_used(self):
        for row in self.records:row['known_at']='2026-11-04T00:00:00+09:00'
        r=self.run_optimizer();self.assertEqual(r['status'],'UNRESOLVED_NO_VALIDATED_INCUMBENT');self.assertTrue(all(x['issues'][0]['code']=='INVALID_SETTLEMENT_EVIDENCE' for x in r['unresolved_candidates']))
    def test_fix_and_commitment_are_preserved(self):
        self.s['calls'][0].update(fixed_etb_is_hard=True,fix_etb_at='2026-11-03T02:00:00+09:00');r=self.run_optimizer()
        self.assertEqual(r['incumbent']['berth_schedule'][0]['etb_at'],'2026-11-03T02:00:00+09:00');self.assertEqual(r['incumbent']['total_demurrage_cents'],18100)
    def test_clear_weather_baseline_is_explicit_and_costed(self):
        self.records=[];r=self.run_optimizer(pricing_policy='CLEAR_WEATHER_BASELINE_V1')
        self.assertEqual(r['status'],'SIMULATION_FEASIBLE',r);self.assertEqual(r['incumbent']['total_demurrage_cents'],12100)
        settlement=r['incumbent']['settlements']['SYN-A'];self.assertEqual(settlement['settlement_policy'],'SECOND_PRECISION_SIMULATION_V1');self.assertEqual(settlement['weather_assumption'],'CLEAR_WEATHER_BASELINE_V1');self.assertNotIn('despatch_cents',settlement['settlement']);self.assertFalse(r['operational_approval'])
    def test_validated_incumbent_survives_subsequent_timeout(self):
        from src.daily_planning.physical_scheduling.service import solve_berth_schedule
        calls=[]
        def solver(*args,**kwargs):
            calls.append(1)
            return solve_berth_schedule(*args,**kwargs) if len(calls)==1 else {'status':'TIME_LIMIT_NO_INCUMBENT'}
        with patch('src.daily_planning.integrated_input.validator.validate',return_value={'data_preflight_pass':True}),patch('src.daily_planning.integrated_v3.optimizer.solve_berth_schedule',side_effect=solver):
            r=optimize_72h(self.s,as_of_at='2026-11-03T00:00:00+09:00',policy=self.p,candidate_etbs=self.etbs,settlement_records=[],pricing_policy='CLEAR_WEATHER_BASELINE_V1',time_limit_seconds=15)
        self.assertEqual(r['status'],'TIME_LIMIT',r);self.assertIsNotNone(r['incumbent']);self.assertEqual(len(r['iterations']),1)
    def test_independent_validator_rejects_cost_and_inventory_tampering(self):
        from src.daily_planning.integrated_v3.independent_validator import validate_simulation_candidate
        r=self.run_optimizer(pricing_policy='CLEAR_WEATHER_BASELINE_V1');candidate=copy.deepcopy(r['incumbent']);self.assertEqual(r['independent_validation']['status'],'VALID')
        candidate['total_demurrage_cents']+=1
        check=validate_simulation_candidate(self.s,candidate,policy=self.p,as_of_at='2026-11-03T00:00:00+09:00',pricing_policy='CLEAR_WEATHER_BASELINE_V1')
        self.assertIn('DEMURRAGE_REPLAY_MISMATCH',[x['code'] for x in check['issues']])
        candidate=copy.deepcopy(r['incumbent']);candidate['tank_validation']['inventories']['TEST-TANK'][-1]['inventory_bbl']='999'
        check=validate_simulation_candidate(self.s,candidate,policy=self.p,as_of_at='2026-11-03T00:00:00+09:00',pricing_policy='CLEAR_WEATHER_BASELINE_V1')
        self.assertIn('INVENTORY_REPLAY_MISMATCH',[x['code'] for x in check['issues']])
    def test_frozen_commitment_is_unchanged(self):
        from src.daily_planning.physical_scheduling.phases import replay
        locked=replay(self.s['calls'][0],self.s['berths'][0],'2026-11-03T02:00:00+09:00',self.p)
        r=self.run_optimizer(commitments=[locked]);self.assertEqual(r['status'],'SIMULATION_FEASIBLE',r)
        self.assertEqual(r['incumbent']['berth_schedule'],[locked]);self.assertEqual(r['independent_validation']['status'],'VALID')
    def test_independent_validator_detects_missing_call(self):
        from src.daily_planning.integrated_v3.independent_validator import validate_simulation_candidate
        r=self.run_optimizer(pricing_policy='CLEAR_WEATHER_BASELINE_V1');candidate=copy.deepcopy(r['incumbent']);candidate['berth_schedule']=[]
        check=validate_simulation_candidate(self.s,candidate,policy=self.p,as_of_at='2026-11-03T00:00:00+09:00',pricing_policy='CLEAR_WEATHER_BASELINE_V1')
        self.assertIn('CALL_COVERAGE_MISMATCH',[x['code'] for x in check['issues']])
    def test_subminute_physical_finish_is_settled_in_approved_simulation(self):
        self.s['berths'][0]['max_transfer_rate_mt_h']='960';self.etbs={'SYN-A':['2026-11-03T01:00:00+09:00']}
        r=self.run_optimizer(pricing_policy='CLEAR_WEATHER_BASELINE_V1')
        self.assertEqual(r['status'],'SIMULATION_FEASIBLE',r);self.assertEqual(r['incumbent']['berth_schedule'][0]['work_end_at'],'2026-11-03T01:01:04+09:00')
        self.assertEqual(r['incumbent']['total_demurrage_cents'],6107);self.assertEqual(r['independent_validation']['status'],'VALID')
    def test_inventory_relaxation_returns_only_exactly_replayed_solution(self):
        r=self.run_optimizer(pricing_policy='CLEAR_WEATHER_BASELINE_V1',inventory_cuts=True,inventory_relaxation=True)
        self.assertEqual(r['status'],'SIMULATION_FEASIBLE',r);self.assertEqual(r['independent_validation']['status'],'VALID')
        tank=r['incumbent']['tank_validation'];self.assertEqual(tank['inventory_relaxation_policy'],'V3_INVENTORY_RELAXATION_V1');self.assertGreater(tank['heuristic_constraint_count'],0)
    def test_reused_physical_model_enumerates_same_costed_domain(self):
        a=self.run_optimizer(pricing_policy='CLEAR_WEATHER_BASELINE_V1')
        b=self.run_optimizer(pricing_policy='CLEAR_WEATHER_BASELINE_V1',reuse_physical_model=True)
        self.assertEqual(b['status'],'SIMULATION_FEASIBLE',b);self.assertTrue(b['domain_exhausted']);self.assertEqual(len(b['iterations']),2);self.assertEqual(a['incumbent']['total_demurrage_cents'],b['incumbent']['total_demurrage_cents']);self.assertEqual(b['independent_validation']['status'],'VALID')
