import itertools
import copy
import unittest
from tests.test_v5_cost_aware import make_case,run_case
from tests.test_v5_physical_integration import START
from src.daily_planning.integrated_v3.coordinator import validate_v5_candidate
from src.daily_planning.integrated_v3.v5_settlement import price_v5_schedule
from src.daily_planning.physical_scheduling.validator import validate_result


class IntegratedInventoryTest(unittest.TestCase):
    def check_oracle(self,count):
        s,p,times=make_case(count);s['tanks'][0]['max_receipt_rate_bbl_per_hour']='100'
        for name in ('U','V'):
            tank=copy.deepcopy(s['tanks'][0]);tank.update(tank_id=name,cargo_group=name,connected_berth_ids=[]);s['tanks'].append(tank)
            flow=copy.deepcopy(s['fixed_process_flows']['segments'][0]);flow['tank_id']=name;s['fixed_process_flows']['segments'].append(flow)
        if count==3:s['calls'][2]['direction']='LOAD'
        physical=run_case(s,p,times)
        if count==2:self.assertEqual(physical['status'],'INVENTORY_VALIDATION_FAILED')
        options=[x['option'] for x in physical['option_cost_breakdown']]
        groups=[[r for r in options if r['call_id']==c['call_id']] for c in s['calls']]
        valid=[];invalid=[]
        for rows in itertools.product(*groups):
            if validate_result(s,list(rows),p)['status']!='VALID':continue
            price=price_v5_schedule(s,list(rows),as_of_at=START,settlement_records=[],pricing_policy='CLEAR_WEATHER_BASELINE_V1')
            if price['status']!='SETTLED':continue
            inv=validate_v5_candidate(s,list(rows),as_of_at=START)
            (valid if inv['status']=='INVENTORY_VALID' else invalid).append(price['total_demurrage_cents'])
        self.assertGreater(len(set(valid)),1)
        if count==2:self.assertLess(min(invalid),min(valid))
        result=run_case(s,p,times,inventory_constraint_mode='EXACT_EVENT_BALANCE_V1')
        self.assertEqual(result['status'],'PHYSICAL_AND_INVENTORY_VALID',result)
        self.assertEqual(result['solver_status'],'OPTIMAL');self.assertEqual(result['total_projected_demurrage_cents'],min(valid))
        self.assertEqual(result['optimality_scope'],'INTEGRATED_FINITE_DOMAIN_OPTIMAL')
    def test_two_vessels_against_independent_oracle(self):self.check_oracle(2)
    def test_three_vessels_against_independent_oracle(self):self.check_oracle(3)
    def test_missing_flow_and_no_feasible_inventory(self):
        s,p,times=make_case(2);s['fixed_process_flows']['segments'][0]['end_at']='2026-11-03T12:00:00+09:00'
        result=run_case(s,p,times,inventory_constraint_mode='EXACT_EVENT_BALANCE_V1')
        self.assertEqual(result['status'],'INVALID_INPUT');self.assertEqual(result['issues'][0]['code'],'INVALID_INVENTORY_MODEL')
        s,p,times=make_case(2);s['tanks'][0]['capacity_bbl']='550'
        result=run_case(s,p,times,inventory_constraint_mode='EXACT_EVENT_BALANCE_V1');self.assertEqual(result['status'],'INFEASIBLE')
    def test_unresolved_settlement_fails_closed(self):
        s,p,times=make_case(2);s['source_input']['data_origin']='NOT_SIMULATION'
        result=run_case(s,p,times,inventory_constraint_mode='EXACT_EVENT_BALANCE_V1')
        self.assertEqual(result['status'],'SETTLEMENT_UNRESOLVED')
