import copy
import itertools
import unittest
from unittest.mock import patch
from ortools.sat.python import cp_model
from tests.test_v5_cost_aware import make_case, run_case
from tests.test_v5_physical_integration import START
from src.daily_planning.integrated_v3.coordinator import validate_v5_candidate
from src.daily_planning.integrated_v3.v5_physical import schedule_v5
from src.daily_planning.physical_scheduling.phases import replay
from src.daily_planning.physical_scheduling.validator import validate_result
from src.daily_planning.integrated_v3.v5_settlement import price_v5_schedule


class IntegratedHardeningTest(unittest.TestCase):
    def setUp(self):
        self.s,self.p,self.times=make_case()
        self.row=replay(self.s['calls'][0],self.s['berths'][0],'2026-11-03T03:00:00+09:00',self.p)

    def carried_case(self,boundary,**changes):
        previous=validate_v5_candidate(self.s,[self.row],as_of_at=START,projection_end_at=boundary)
        self.assertEqual(previous['status'],'INVENTORY_VALID',previous)
        state=previous['tank_result']['end_state'];carries=copy.deepcopy(state['carried_transfers'])
        if changes.get('tamper'):carries[0]['transfer_fingerprint']='incorrect'
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',return_value=copy.deepcopy(self.s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}),patch('src.daily_planning.integrated_v3.v5_physical.prepare_pilotage_policy',side_effect=lambda s,c,e,p,*args:(p,None)):
            r=schedule_v5(self.s,data_dir='synthetic',call_ids=list(self.times),as_of_at=boundary,policy=self.p,candidate_etbs=self.times,objective_mode='MIN_TOTAL_DEMURRAGE',pricing_policy='CLEAR_WEATHER_BASELINE_V1',inventory_constraint_mode='EXACT_EVENT_BALANCE_V1',physical_commitments=[self.row],initial_inventory=state['inventory_bbl'],carried_transfers=carries)
        return r,state

    def test_carry_protected_and_new_call(self):
        r,state=self.carried_case('2026-11-03T03:30:00+09:00')
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r);self.assertIn(self.row,r['selected_schedule'])
        self.assertEqual(r['inventory_result']['tank_result']['end_state']['inventory_bbl']['T'],'700')
        self.assertEqual(len(state['carried_transfers']),1)
        self.assertFalse(any(x['option']['call_id']==self.row['call_id'] for x in r['option_cost_breakdown']))
        prices=[];boundary=state['at']
        for item in r['option_cost_breakdown']:
            rows=[self.row,item['option']]
            if validate_result(self.s,rows,self.p)['status']!='VALID':continue
            inv=validate_v5_candidate(self.s,rows,as_of_at=boundary,initial_inventory=state['inventory_bbl'],carried_transfers=state['carried_transfers'],physical_commitments=[self.row])
            if inv['status']!='INVENTORY_VALID':continue
            priced=price_v5_schedule(self.s,rows,as_of_at=boundary,settlement_records=[],pricing_policy='CLEAR_WEATHER_BASELINE_V1')
            if priced['status']=='SETTLED':prices.append(priced['total_demurrage_cents'])
        self.assertTrue(prices);self.assertEqual(r['total_projected_demurrage_cents'],min(prices))

    def test_altered_carried_fingerprint_is_rejected(self):
        r,_=self.carried_case('2026-11-03T03:30:00+09:00',tamper=True)
        self.assertEqual(r['status'],'INVALID_INPUT');self.assertEqual(r['issues'][0]['code'],'INVALID_INVENTORY_MODEL')

    def test_completed_cargo_is_not_replayed(self):
        boundary=self.row['work_end_at'];r,_=self.carried_case(boundary)
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r)
        self.assertEqual(r['inventory_result']['tank_result']['end_state']['inventory_bbl']['T'],'700')

    def test_integrated_feasible_is_not_reported_optimal(self):
        original=cp_model.CpSolver.Solve
        def feasible(solver,model,*args,**kwargs):
            result=original(solver,model,*args,**kwargs)
            return cp_model.FEASIBLE if result==cp_model.OPTIMAL else result
        with patch.object(cp_model.CpSolver,'Solve',feasible):r=run_case(self.s,self.p,self.times,inventory_constraint_mode='EXACT_EVENT_BALANCE_V1')
        self.assertEqual(r['solver_status'],'FEASIBLE');self.assertEqual(r['optimality_scope'],'INTEGRATED_FINITE_DOMAIN_INCUMBENT')

    def test_validator_disagreement_blocks_optimality(self):
        bad={'status':'TANK_SUBPROBLEM_NOT_VALIDATED','issues':[{'code':'INVENTORY_OUT_OF_BOUNDS'}]}
        with patch('src.daily_planning.integrated_v3.v5_physical.validate_v5_candidate',return_value=bad):r=run_case(self.s,self.p,self.times,inventory_constraint_mode='EXACT_EVENT_BALANCE_V1')
        self.assertEqual(r['status'],'INVENTORY_MODEL_VALIDATION_MISMATCH');self.assertEqual(r['optimality_scope'],'NOT_VALIDATED')

    def test_inventory_bounds_are_hard(self):
        self.s['tanks'][0]['capacity_bbl']='550'
        r=run_case(self.s,self.p,self.times,inventory_constraint_mode='EXACT_EVENT_BALANCE_V1');self.assertEqual(r['status'],'INFEASIBLE')
        self.s['tanks'][0]['capacity_bbl']='1000';self.s['tanks'][0]['min_inventory_bbl']='450'
        for c in self.s['calls']:c['direction']='LOAD'
        r=run_case(self.s,self.p,self.times,inventory_constraint_mode='EXACT_EVENT_BALANCE_V1');self.assertEqual(r['status'],'INFEASIBLE')

    def test_gross_receipt_and_dispatch_are_hard(self):
        for direction,field in [('DISCHARGE','max_receipt_rate_bbl_per_hour'),('LOAD','max_dispatch_rate_bbl_per_hour')]:
            s,p,times=make_case()
            for c in s['calls']:c['direction']=direction
            s['tanks'][0][field]='99'
            r=run_case(s,p,times,inventory_constraint_mode='EXACT_EVENT_BALANCE_V1');self.assertEqual(r['status'],'INFEASIBLE',r)

    def test_projection_cannot_truncate_transfer(self):
        r=run_case(self.s,self.p,self.times,inventory_constraint_mode='EXACT_EVENT_BALANCE_V1',projection_end_at='2026-11-03T03:30:00+09:00')
        self.assertEqual(r['status'],'INVALID_INPUT');self.assertEqual(r['issues'][0]['code'],'INVALID_INVENTORY_MODEL')

    def test_no_incumbent_timeout_is_not_feasible(self):
        original=cp_model.CpSolver.Solve
        def unknown(solver,model,*args,**kwargs):
            original(solver,model,*args,**kwargs)
            return cp_model.UNKNOWN
        with patch.object(cp_model.CpSolver,'Solve',unknown):r=run_case(self.s,self.p,self.times,inventory_constraint_mode='EXACT_EVENT_BALANCE_V1')
        self.assertEqual(r['status'],'TIME_LIMIT_NO_INCUMBENT');self.assertEqual(r['combined_validation'],'NOT_VALIDATED')

    def test_fractional_cargo_and_signed_flow(self):
        c=self.s['calls'][0];c.update(cargo_quantity_mt='1/7',cargo_volume_bbl='10/7')
        source=self.s['workbook_tables']['contracts']['rows'][0];source['quantity_mt']='1/7'
        self.s['calls'][1]['direction']='LOAD'
        f=self.s['fixed_process_flows']['segments'][0];f['end_at']='2026-11-03T12:00:00+09:00';g=copy.deepcopy(f)
        f['fixed_net_flow_bbl_per_hour']='1/10';g.update(start_at=f['end_at'],end_at='2026-11-09T00:00:00+09:00',fixed_net_flow_bbl_per_hour='-1/10');self.s['fixed_process_flows']['segments'].append(g)
        r=run_case(self.s,self.p,self.times,inventory_constraint_mode='EXACT_EVENT_BALANCE_V1')
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r)
        cargo=r['inventory_result']['tank_result']['quantity_reconciliation'][c['call_id']]
        self.assertTrue(cargo['matched']);self.assertEqual(cargo['segments_bbl'],'10/7')
        segments=next(x['segments'] for x in r['inventory_result']['tank_result']['transfer_definitions'] if x['call_id']==c['call_id'])
        self.assertEqual(segments[-1]['rate_bbl_per_hour'],'300/7')
        options=[x['option'] for x in r['option_cost_breakdown']];groups=[[o for o in options if o['call_id']==c['call_id']] for c in self.s['calls']];prices=[]
        for rows in itertools.product(*groups):
            if validate_result(self.s,list(rows),self.p)['status']!='VALID':continue
            inv=validate_v5_candidate(self.s,list(rows),as_of_at=START)
            if inv['status']!='INVENTORY_VALID':continue
            cost=price_v5_schedule(self.s,list(rows),as_of_at=START,settlement_records=[],pricing_policy='CLEAR_WEATHER_BASELINE_V1')
            if cost['status']=='SETTLED':prices.append(cost['total_demurrage_cents'])
        self.assertTrue(prices);self.assertEqual(r['total_projected_demurrage_cents'],min(prices))

    def test_model_scaling_failure_never_falls_back(self):
        from src.daily_planning.integrated_v3.v5_inventory_constraints import InventoryScalingError
        with patch('src.daily_planning.integrated_v3.v5_inventory_constraints.add_inventory_constraints',side_effect=InventoryScalingError('overflow')):
            r=run_case(self.s,self.p,self.times,inventory_constraint_mode='EXACT_EVENT_BALANCE_V1')
        self.assertEqual(r['status'],'INVALID_INPUT');self.assertEqual(r['issues'][0]['code'],'INVALID_INVENTORY_MODEL');self.assertEqual(r['combined_validation'],'NOT_VALIDATED')
