import copy
import itertools
import unittest
from unittest.mock import patch
from tests.test_v5_cost_aware import make_case
from tests.test_v5_physical_integration import START
from src.daily_planning.integrated_v3.v5_physical import schedule_v5
from src.daily_planning.physical_scheduling.validator import validate_result
from src.daily_planning.integrated_v3.coordinator import validate_v5_candidate
from src.daily_planning.integrated_v3.v5_settlement import price_v5_schedule


class CostAwareCandidatesTest(unittest.TestCase):
    def setUp(self):
        self.s,self.p,self.times=make_case(3)
        for tid in ('U','V'):
            tank=copy.deepcopy(self.s['tanks'][0]);tank.update(tank_id=tid,cargo_group=tid,connected_berth_ids=[]);self.s['tanks'].append(tank)
            flow=copy.deepcopy(self.s['fixed_process_flows']['segments'][0]);flow['tank_id']=tid;self.s['fixed_process_flows']['segments'].append(flow)
        for call,minutes in zip(self.s['calls'],(0,30,60)):
            call['eta_at']=f'2026-11-03T{minutes//60:02}:{minutes%60:02}:00+09:00'
    def run_auto(self,**kwargs):
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *a:copy.deepcopy(self.s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}),patch('src.daily_planning.integrated_v3.v5_physical.prepare_pilotage_policy',side_effect=lambda s,c,e,p,*a:(p,None)):
            return schedule_v5(self.s,data_dir='SYNTHETIC',call_ids=list(self.times),as_of_at=START,policy=self.p,candidate_generation_mode='AUTO_ADAPTIVE_CANDIDATES',objective_mode='MIN_TOTAL_DEMURRAGE',inventory_constraint_mode='EXACT_EVENT_BALANCE_V1',pricing_policy='CLEAR_WEATHER_BASELINE_V1',time_limit_seconds=15,max_candidates_per_pair=6,**kwargs)
    def test_improves_initial_optimal_domain_additively(self):
        base=self.run_auto();r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1')
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r)
        self.assertEqual(r['cost_improvement_history'][0]['solver_status'],'OPTIMAL')
        self.assertEqual(base['total_projected_demurrage_cents'],r['initial_total_demurrage_cents'])
        self.assertLess(r['best_total_demurrage_cents'],r['initial_total_demurrage_cents'])
        for cid,pairs in base['candidates_by_call_and_berth'].items():
            for bid,times in pairs.items():self.assertTrue(set(times)<=set(r['candidates_by_call_and_berth'][cid][bid]))
        self.assertEqual(r['physical_validation']['status'],'VALID');self.assertEqual(r['inventory_validation'],'INVENTORY_VALID');self.assertEqual(r['settlement_validation'],'SETTLED')
        self.assertTrue(all(len(v)<=6 for pairs in r['candidates_by_call_and_berth'].values() for v in pairs.values()))
    def test_determinism_and_disabled_compatibility(self):
        a=self.run_auto(candidate_improvement_mode='COST_AWARE_V1');b=self.run_auto(candidate_improvement_mode='COST_AWARE_V1')
        self.assertEqual(a['candidates_by_call_and_berth'],b['candidates_by_call_and_berth']);self.assertEqual(a['best_total_demurrage_cents'],b['best_total_demurrage_cents'])
    def test_frozen_domain_is_rejected(self):
        r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1',frozen_candidate_domain={})
        self.assertEqual(r['status'],'INVALID_INPUT');self.assertEqual(r['issues'][0]['code'],'INVALID_IMPROVEMENT_MODE')
    def test_unresolved_settlement_not_priced(self):
        self.s['source_input']['data_origin']='NOT_SIMULATION'
        r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1')
        self.assertEqual(r['status'],'SETTLEMENT_UNRESOLVED');self.assertNotIn('best_total_demurrage_cents',r)
    def test_unresolved_operational_policy_remains_unresolved(self):
        self.p={};r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1')
        self.assertEqual(r['status'],'UNSUPPORTED_OPERATIONAL_POLICY')
    def test_new_domain_retains_inventory_validation(self):
        self.s['tanks'][0]['max_receipt_rate_bbl_per_hour']='100'
        r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1')
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r)
        options=[x['option'] for x in r['final_solver_result']['option_cost_breakdown']]
        groups=[[o for o in options if o['call_id']==c['call_id']] for c in self.s['calls']]
        invalid=False
        for rows in itertools.product(*groups):
            if validate_result(self.s,list(rows),self.p)['status']!='VALID':continue
            if validate_v5_candidate(self.s,list(rows),as_of_at=START)['status']!='INVENTORY_VALID':invalid=True;break
        self.assertTrue(invalid);self.assertEqual(r['inventory_validation'],'INVENTORY_VALID')
    def test_final_domain_independent_exhaustive_optimum(self):
        r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1')
        self.assertTrue(r['final_domain_optimality_proven'])
        options=[x['option'] for x in r['final_solver_result']['option_cost_breakdown']]
        groups=[[o for o in options if o['call_id']==c['call_id']] for c in self.s['calls']]
        best=None
        for rows in itertools.product(*groups):
            if validate_result(self.s,list(rows),self.p)['status']!='VALID':continue
            inv=validate_v5_candidate(self.s,list(rows),as_of_at=START)
            if inv['status']!='INVENTORY_VALID':continue
            cost=price_v5_schedule(self.s,list(rows),as_of_at=START,settlement_records=[],pricing_policy='CLEAR_WEATHER_BASELINE_V1')
            if cost['status']=='SETTLED':best=cost['total_demurrage_cents'] if best is None else min(best,cost['total_demurrage_cents'])
        self.assertIsNotNone(best);self.assertEqual(r['best_total_demurrage_cents'],best)
    def test_later_failure_preserves_incumbent(self):
        from src.daily_planning.integrated_v3.v5_physical import solve_berth_schedule
        seen=[]
        def fail_later(*args,**kwargs):
            seen.append(1)
            return solve_berth_schedule(*args,**kwargs) if len(seen)==1 else {'status':'TIME_LIMIT_NO_INCUMBENT','issues':[]}
        with patch('src.daily_planning.integrated_v3.v5_physical.solve_berth_schedule',side_effect=fail_later):r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1')
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID');self.assertEqual(r['best_incumbent_round'],0);self.assertFalse(r['final_domain_optimality_proven']);self.assertEqual(r['optimality_scope'],'BEST_VALIDATED_INCUMBENT_EXPANDED_DOMAIN_UNPROVEN')
    def test_round_budgets_share_deadline(self):
        import time
        from src.daily_planning.integrated_v3.v5_physical import solve_berth_schedule
        budgets=[];started=time.perf_counter()
        def capture(*args,**kwargs):
            budgets.append((kwargs['time_limit_seconds'],time.perf_counter()-started))
            return solve_berth_schedule(*args,**kwargs)
        with patch('src.daily_planning.integrated_v3.v5_physical.solve_berth_schedule',side_effect=capture):r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1')
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID');self.assertGreaterEqual(len(budgets),2)
        for budget,elapsed in budgets:self.assertLessEqual(budget,15-elapsed+0.1)
        self.assertLess(budgets[0][0],15)
    def test_timeout_after_partial_expansion_revokes_old_proof(self):
        from src.daily_planning.integrated_v3.v5_candidates import CandidatePool
        original=CandidatePool.expand_cost
        def partial(pool,*args):
            self.assertGreater(original(pool,*args),0)
            raise TimeoutError('injected timeout after domain grew')
        with patch.object(CandidatePool,'expand_cost',partial):r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1')
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID');self.assertEqual(r['stopping_reason'],'TIME_BUDGET_EXHAUSTED')
        self.assertFalse(r['final_domain_optimality_proven']);self.assertEqual(r['best_incumbent_round'],0)
    def test_fix_and_protected_rows_remain_unchanged(self):
        from src.daily_planning.physical_scheduling.phases import replay
        fixed=self.s['calls'][0];fixed.update(fixed_etb_is_hard=True,fix_etb_at='2026-11-03T00:01:00+09:00')
        row=replay(fixed,self.s['berths'][0],fixed['fix_etb_at'],self.p)
        r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1',physical_commitments=[row])
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertIn(row,r['selected_schedule']);self.assertEqual(r['selected_etbs'][fixed['call_id']],fixed['fix_etb_at'])
    def test_total_cap_and_round_limit(self):
        r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1',max_total_candidates=12)
        self.assertLessEqual(r['final_candidate_count'],12);self.assertLessEqual(r['candidate_expansion_rounds'],2)
    def test_zero_improvement_rounds(self):
        r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1',max_improvement_rounds=0)
        self.assertEqual(r['best_total_demurrage_cents'],r['initial_total_demurrage_cents']);self.assertEqual(r['candidate_expansion_rounds'],0)
    def test_five_calls_no_improvement_is_reported(self):
        self.s,self.p,self.times=make_case(5)
        for call,minutes in zip(self.s['calls'],(540,570,600,630,660)):
            call['eta_at']=f'2026-11-03T{minutes//60:02}:{minutes%60:02}:00+09:00'
        for tid in ('U','V'):
            tank=copy.deepcopy(self.s['tanks'][0]);tank.update(tank_id=tid,cargo_group=tid,connected_berth_ids=[]);self.s['tanks'].append(tank)
            flow=copy.deepcopy(self.s['fixed_process_flows']['segments'][0]);flow['tank_id']=tid;self.s['fixed_process_flows']['segments'].append(flow)
        r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1')
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertEqual(len(r['selected_schedule']),5)
        self.assertEqual(r['best_total_demurrage_cents'],r['initial_total_demurrage_cents'])
