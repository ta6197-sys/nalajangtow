import copy
import unittest
from src.daily_planning.tank.v3_optimization import solve_v3_allocation

class TankPolicySearchTest(unittest.TestCase):
    def fixture(self,second_tank):
        from tests.test_v3_tank_inventory import V3InventoryTests
        base=V3InventoryTests();base.setUp();s=base.s
        s['calls'].append(dict(s['calls'][0],call_id='D'))
        s['berths'].append(dict(s['berths'][0],berth_id='B2'))
        s['tanks'][0]['connected_berth_ids'].append('B2')
        if second_tank:
            s['tanks'].append(dict(s['tanks'][0],tank_id='U'))
            s['berths'][1]['connected_tank_ids']='T,U'
            s['fixed_process_flows']['segments'] += [dict(x,tank_id='U') for x in s['fixed_process_flows']['segments']]
        first=dict(base.x[0]);first.pop('tank_id');first['eligible_tank_ids']=['T']
        second=dict(first,call_id='D',berth_id='B2',eligible_tank_ids=['T','U'] if second_tank else ['T'])
        return s,[first,second]
    def solve(self,s,x):
        return solve_v3_allocation(s,x,start_at='2026-11-03T00:00:00+09:00',end_at='2026-11-04T01:00:00+09:00',time_limit_seconds=15)
    def test_only_ambiguous_choice_is_not_declared_infeasible(self):
        s,x=self.fixture(False);before=copy.deepcopy((s,x));r=self.solve(s,x)
        self.assertEqual(r['status'],'POLICY_UNRESOLVED',r);self.assertGreater(r['policy_dependent_assignments'],0);self.assertEqual((s,x),before)
    def test_distinct_tanks_avoid_policy_ambiguity(self):
        s,x=self.fixture(True);r=self.solve(s,x)
        self.assertEqual(r['status'],'VALID',r);self.assertEqual({a['tank_id'] for a in r['assignments']},{'T','U'});self.assertFalse(r['operational_approval'])
    def test_explicit_forbid_can_prove_infeasible(self):
        s,x=self.fixture(False);r=solve_v3_allocation(s,x,start_at='2026-11-03T00:00:00+09:00',end_at='2026-11-04T01:00:00+09:00',same_tank_policy='FORBID',time_limit_seconds=15)
        self.assertEqual(r['status'],'INFEASIBLE',r)
    def test_inventory_cuts_keep_feasible_alternative(self):
        from tests.test_v3_tank_inventory import V3InventoryTests
        f=V3InventoryTests();f.setUp();s=f.s
        s['tanks'].append(dict(s['tanks'][0],tank_id='U',capacity_bbl='300'))
        s['tanks'][0]['capacity_bbl']='120';s['berths'][0]['connected_tank_ids']='T,U'
        s['fixed_process_flows']['segments'] += [dict(row,tank_id='U') for row in s['fixed_process_flows']['segments']]
        x=dict(f.x[0]);x.pop('tank_id');x['eligible_tank_ids']=['T','U']
        r=solve_v3_allocation(s,[x],start_at='2026-11-03T00:00:00+09:00',end_at='2026-11-04T01:00:00+09:00',time_limit_seconds=15,inventory_cuts=True)
        self.assertEqual(r['status'],'VALID',r);self.assertEqual(r['assignments'][0]['tank_id'],'U')
    def test_inventory_cut_preserves_rescue_by_other_vessel(self):
        from tests.test_v3_tank_inventory import V3InventoryTests
        f=V3InventoryTests();f.setUp();s=f.s;s['calls'][0]['direction']='LOAD'
        s['calls'].append(dict(s['calls'][0],call_id='D',direction='DISCHARGE'))
        s['berths'][0]['allowed_operations']='LOAD,DISCHARGE'
        s['berths'].append(dict(s['berths'][0],berth_id='B2',connected_tank_ids='T,U'))
        s['tanks'][0].update(initial_inventory_bbl='0',connected_berth_ids=['B','B2'])
        s['tanks'].append(dict(s['tanks'][0],tank_id='U',connected_berth_ids=['B2']))
        for flow in s['fixed_process_flows']['segments']:flow['fixed_net_flow_bbl_per_hour']='0'
        s['fixed_process_flows']['segments'] += [dict(flow,tank_id='U') for flow in s['fixed_process_flows']['segments']]
        first=dict(f.x[0]);first.pop('tank_id');first['eligible_tank_ids']=['T']
        second=dict(first,call_id='D',berth_id='B2',eligible_tank_ids=['T','U'])
        r=solve_v3_allocation(s,[first,second],start_at='2026-11-03T00:00:00+09:00',end_at='2026-11-04T01:00:00+09:00',same_tank_policy='ALLOW',time_limit_seconds=15,inventory_cuts=True)
        self.assertEqual(r['status'],'VALID',r);self.assertEqual([v['tank_id'] for v in r['assignments']],['T','T'])
