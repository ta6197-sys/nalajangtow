import copy
import json
import math
from datetime import timedelta
from fractions import Fraction
from pathlib import Path
import unittest
from unittest.mock import patch

from src.daily_planning.tank.v5_inventory import validate_v5_inventory
from src.daily_planning.integrated_v3.coordinator import validate_v5_candidate,validate_candidate
from src.daily_planning.integrated_input.validator import timestamp,number

START='2026-11-03T00:00:00+09:00'
END='2026-11-05T00:00:00+09:00'

def at(hours):return (timestamp(START)+timedelta(hours=hours)).isoformat()

def fixture():
    tank={'tank_id':'T','cargo_group':'X','connected_berth_ids':['B'],'initial_inventory_bbl':'500','min_inventory_bbl':'0','capacity_bbl':'1000','max_receipt_rate_bbl_per_hour':'100','max_dispatch_rate_bbl_per_hour':'100'}
    berth={'berth_id':'B','active':'Y','connected_tank_ids':'T','allowed_cargo_groups':'X','allowed_operations':'LOAD,DISCHARGE','max_transfer_rate_mt_h':'100'}
    return {'scenario_id':'V5_3TANK','calls':[],'berths':[berth],'tanks':[tank],'fixed_process_flows':{'inventory_as_of_at':START,'segments':[{'tank_id':'T','start_at':START,'end_at':END,'fixed_net_flow_bbl_per_hour':'0'}]}}

def add(s,cid,direction='DISCHARGE',rate=50,start=1,end=2):
    quantity=Fraction(rate)*Fraction(str(end-start))
    s['calls'].append({'call_id':cid,'cargo_group':'X','direction':direction,'cargo_quantity_mt':str(quantity),'cargo_volume_bbl':str(quantity),'physical':{'bbl_per_mt':'1'},'fixed_etb_is_hard':False})
    return {'call_id':cid,'berth_id':'B','start_at':at(start),'end_at':at(end),'segments':[{'start_at':at(start),'end_at':at(end),'rate_bbl_per_hour':str(rate)}]}

class V5InventoryTests(unittest.TestCase):
    def setUp(self):self.s=fixture();self.x=[]
    def run_inv(self,**kw):return validate_v5_inventory(self.s,self.x,start_at=kw.pop('start_at',START),end_at=kw.pop('end_at',at(4)),**kw)
    def assert_valid(self,r):self.assertEqual(r['status'],'VALID',r.get('issues'))
    def assert_code(self,r,code):self.assertIn(code,[e['code'] for e in r['issues']],r)
    def test_single_discharge_and_exact_quantity(self):
        self.x=[add(self.s,'A')];r=self.run_inv();self.assert_valid(r);self.assertEqual(r['end_state']['inventory_bbl']['T'],'550');self.assertTrue(r['quantity_reconciliation']['A']['matched'])
    def test_single_load(self):
        self.x=[add(self.s,'A','LOAD')];r=self.run_inv();self.assert_valid(r);self.assertEqual(r['end_state']['inventory_bbl']['T'],'450')
    def test_simultaneous_receipt_dispatch(self):
        self.x=[add(self.s,'A'),add(self.s,'B','LOAD',40)];r=self.run_inv();self.assert_valid(r);self.assertEqual(r['end_state']['inventory_bbl']['T'],'510')
    def test_simultaneous_receipts_within_gross_limit(self):
        self.x=[add(self.s,'A',rate=60),add(self.s,'B',rate=40)];r=self.run_inv(same_tank_policy='ALLOW');self.assert_valid(r);self.assertEqual(r['gross_vessel_rate_maxima']['T']['receipt_bbl_per_hour'],'100')
    def test_simultaneous_loads_within_gross_limit(self):
        self.x=[add(self.s,'A','LOAD',60),add(self.s,'B','LOAD',40)];r=self.run_inv(same_tank_policy='ALLOW');self.assert_valid(r);self.assertEqual(r['gross_vessel_rate_maxima']['T']['dispatch_bbl_per_hour'],'100')
    def test_fixed_flow_sign_and_no_shared_throughput_assumption(self):
        self.s['fixed_process_flows']['segments'][0]['fixed_net_flow_bbl_per_hour']='-10';self.x=[add(self.s,'A',rate=100)];r=self.run_inv();self.assert_valid(r);self.assertEqual(r['external_net_flow_totals_bbl']['T'],'-40');self.assertEqual(r['end_state']['inventory_bbl']['T'],'560')
    def test_fractional_quantity(self):
        self.x=[add(self.s,'A',rate=1,start=1,end=1.5)];r=self.run_inv();self.assert_valid(r);self.assertEqual(r['end_state']['inventory_bbl']['T'],'1001/2')
    def test_carry_24h_and_arbitrary_boundary(self):
        self.x=[add(self.s,'A',rate=20,start=23,end=25)];r=self.run_inv(end_at=at(24));self.assert_valid(r);self.assertEqual(r['end_state']['carried_transfers'][0]['remaining_bbl'],'20')
        q=self.run_inv(start_at=at(24),end_at=at(26),initial_inventory=r['end_state']['inventory_bbl'],carried_transfers=r['end_state']['carried_transfers']);self.assert_valid(q);self.assertEqual(q['end_state']['inventory_bbl']['T'],'540')
        a=self.run_inv(end_at=at(23.5));self.assert_valid(a);self.assertEqual(a['end_state']['carried_transfers'][0]['remaining_bbl'],'30')
        b=self.run_inv(start_at=at(23.5),end_at=at(26),initial_inventory=a['end_state']['inventory_bbl'],carried_transfers=a['end_state']['carried_transfers']);self.assert_valid(b);self.assertEqual(b['end_state']['inventory_bbl']['T'],'540')
    def test_unique_mapping_bypasses_solver(self):
        self.x=[add(self.s,'A')]
        with patch('src.daily_planning.tank.v3_optimization.solve_v3_allocation',side_effect=AssertionError('MILP called')):
            r=self.run_inv();self.assert_valid(r);self.assertTrue(r['solver_bypassed']);self.assertEqual(r['assignments'][0]['tank_id'],'T')
    def test_coordinator_routes_v5_without_milp(self):
        add(self.s,'A');row={'call_id':'A','berth_id':'B','etb_at':at(1),'work_intervals':[[at(1),at(1.5)]]}
        with patch('src.daily_planning.integrated_v3.coordinator.solve_v3_allocation',side_effect=AssertionError('MILP called')):
            r=validate_candidate(self.s,[row],policy={},as_of_at=START,projection_end_at=at(4))
        self.assertEqual(r['status'],'INVENTORY_VALID',r);self.assertEqual(r['physical_validation'],'NOT_EVALUATED')
        protected=validate_candidate(self.s,[row],policy={},as_of_at=START,projection_end_at=at(4),protected_call_ids=['A'])
        self.assert_code(protected,'INVALID_CARRYOVER_STATE')
    def test_underflow(self):
        self.s['tanks'][0]['initial_inventory_bbl']='10';self.x=[add(self.s,'A','LOAD')];self.assert_code(self.run_inv(),'INVENTORY_OUT_OF_BOUNDS')
    def test_overflow(self):
        self.s['tanks'][0]['initial_inventory_bbl']='990';self.x=[add(self.s,'A')];self.assert_code(self.run_inv(),'INVENTORY_OUT_OF_BOUNDS')
    def test_gross_receipt_exceeded_despite_acceptable_net(self):
        self.x=[add(self.s,'A',rate=70),add(self.s,'B',rate=50),add(self.s,'C','LOAD',40)];self.assert_code(self.run_inv(same_tank_policy='ALLOW'),'RECEIPT_RATE_EXCEEDED')
    def test_gross_dispatch_exceeded(self):
        self.x=[add(self.s,'A','LOAD',70),add(self.s,'B','LOAD',50)];self.assert_code(self.run_inv(same_tank_policy='ALLOW'),'DISPATCH_RATE_EXCEEDED')
    def test_wrong_tank_cargo(self):
        self.x=[add(self.s,'A')];self.x[0]['tank_id']='WRONG';self.assert_code(self.run_inv(),'MISSING_TANK_CONNECTION')
    def test_missing_connection(self):
        self.x=[add(self.s,'A')];self.s['tanks'][0]['connected_berth_ids']=[];self.assert_code(self.run_inv(),'MISSING_TANK_CONNECTION')
    def test_ambiguous_tanks(self):
        self.x=[add(self.s,'A')];self.s['tanks'].append(dict(self.s['tanks'][0],tank_id='T2'));self.assert_code(self.run_inv(),'MISSING_TANK_CONNECTION')
    def test_wrong_quantity(self):
        self.x=[add(self.s,'A')];self.x[0]['segments'][0]['rate_bbl_per_hour']='49';self.assert_code(self.run_inv(),'INVALID_TRANSFER_QUANTITY')
    def test_flow_gap(self):
        self.s['fixed_process_flows']['segments'][0]['start_at']=at(1);self.assert_code(self.run_inv(),'INVALID_FLOW_COVERAGE')
    def test_duplicate_carried_transfer(self):
        self.x=[add(self.s,'A',start=23,end=25)];r=self.run_inv(end_at=at(24));carry=r['end_state']['carried_transfers'];self.assert_code(self.run_inv(start_at=at(24),end_at=at(26),initial_inventory=r['end_state']['inventory_bbl'],carried_transfers=carry*2),'INVALID_CARRYOVER_STATE')
    def test_invalid_carry_inventory(self):
        self.assert_code(self.run_inv(start_at=at(1),initial_inventory={'T':'1001'}),'INVALID_CARRYOVER_STATE')
    def test_changed_protected_transfer(self):
        self.x=[add(self.s,'A')];committed=[dict(self.x[0],tank_id='T')];self.x[0]['end_at']=at(3);self.assert_code(self.run_inv(commitments=committed),'INVALID_CARRYOVER_STATE')
    def test_changed_carry_transfer(self):
        self.x=[add(self.s,'A',start=23,end=25)];r=self.run_inv(end_at=at(24));self.x[0]['segments'][0]['rate_bbl_per_hour']='49';self.assert_code(self.run_inv(start_at=at(24),end_at=at(26),initial_inventory=r['end_state']['inventory_bbl'],carried_transfers=r['end_state']['carried_transfers']),'INVALID_CARRYOVER_STATE')
    def test_unresolved_concurrency(self):
        self.x=[add(self.s,'A'),add(self.s,'B','LOAD')];self.assert_code(self.run_inv(same_tank_policy=None),'POLICY_UNRESOLVED')

class RealV5Test(unittest.TestCase):
    def test_three_groups_independent_candidate_schedule(self):
        p=Path(__file__).resolve().parents[1]/'results/v5_preflight/2207668409df0d34731476c5/normalized_snapshot.json'
        if not p.exists():self.skipTest('Validated real v5 snapshot unavailable')
        s=json.loads(p.read_text());rows=[]
        for group in ('CRUDE','PRODUCT','LPG'):
            c=min((c for c in s['calls'] if c['cargo_group']==group and not c['fixed_etb_is_hard']),key=lambda c:c['eta_at'])
            b=next(b for b in s['berths'] if b['berth_id']==c['eligible_berth_ids'][0]);a=timestamp(c['eta_at']);rate=number(b['max_transfer_rate_mt_h'])*number(c['physical']['bbl_per_mt']);secs=math.ceil(number(c['cargo_volume_bbl'])*3600/rate);z=a+timedelta(seconds=secs)
            rows.append({'call_id':c['call_id'],'berth_id':b['berth_id'],'etb_at':a.isoformat(),'work_intervals':[[a.isoformat(),z.isoformat()]]})
        r=validate_v5_candidate(s,rows,as_of_at=START,projection_end_at=at(72))
        self.assertEqual(r['status'],'INVENTORY_VALID',r.get('issues'));self.assertEqual({x['tank_id'] for x in r['tank_result']['assignments']},{'TANK-01','TANK-02','TANK-03'});self.assertTrue(r['tank_result']['solver_bypassed']);self.assertEqual(r['physical_validation'],'NOT_EVALUATED')
