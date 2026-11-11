import copy
import unittest

from src.daily_planning.tank.v3_capacity_bound import filter_tank_pairs


class CapacityBoundTests(unittest.TestCase):
    def setUp(self):
        self.start='2026-11-03T00:00:00+09:00'
        self.end='2026-11-03T01:00:00+09:00'
        self.snapshot={'calls':[{'call_id':'C','cargo_group':'PRODUCT','direction':'DISCHARGE','cargo_volume_bbl':'101'}],
                       'tanks':[{'tank_id':'T','min_inventory_bbl':'0','capacity_bbl':'100'}],
                       'fixed_process_flows':{'segments':[{'tank_id':'T','start_at':self.start,'end_at':self.end,'fixed_net_flow_bbl_per_hour':'0'}]}}
        self.rows=[{'call_id':'C','start_at':self.start,'end_at':self.end,
                    'segments':[{'start_at':self.start,'end_at':self.end,'rate_bbl_per_hour':'101'}]}]
        self.pairs=[(0,'T')]

    def apply(self):
        return filter_tank_pairs(self.snapshot,self.rows,self.pairs)

    def test_impossible_and_input_immutability(self):
        before=copy.deepcopy((self.snapshot,self.rows))
        self.assertEqual(self.apply(),([],1))
        self.assertEqual(before,(self.snapshot,self.rows))

    def test_fixed_outflow_and_exact_boundary(self):
        self.snapshot['fixed_process_flows']['segments'][0]['fixed_net_flow_bbl_per_hour']='-1'
        self.assertEqual(self.apply(),(self.pairs,0))
        self.snapshot['fixed_process_flows']['segments'][0]['fixed_net_flow_bbl_per_hour']='-1/2'
        self.assertEqual(self.apply(),([],1))

    def test_concurrent_load_disables_filter(self):
        self.snapshot['calls'].append({'call_id':'L','cargo_group':'PRODUCT','direction':'LOAD'})
        self.assertEqual(filter_tank_pairs(self.snapshot,self.rows,self.pairs,carried_transfers=[{'call_id':'L'}]),(self.pairs,0))
        self.assertEqual(filter_tank_pairs(self.snapshot,self.rows,self.pairs,commitments=[{'call_id':'UNKNOWN'}]),(self.pairs,0))

    def test_started_transfer_is_not_pruned(self):
        self.assertEqual(filter_tank_pairs(self.snapshot,self.rows,self.pairs,carried_transfers=[{'call_id':'C'}]),(self.pairs,0))

    def test_incomplete_or_overlapping_flow_never_prunes(self):
        flow=self.snapshot['fixed_process_flows']['segments'][0]
        flow['end_at']='2026-11-03T00:59:59+09:00'
        self.assertEqual(self.apply(),(self.pairs,0))
        flow['end_at']=self.end
        self.snapshot['fixed_process_flows']['segments'].append(copy.deepcopy(flow))
        self.assertEqual(self.apply(),(self.pairs,0))

    def test_unreconciled_cargo_and_subsecond_never_prune(self):
        self.rows[0]['segments'][0]['rate_bbl_per_hour']='100'
        self.assertEqual(self.apply(),(self.pairs,0))
        self.rows[0]['segments'][0]['rate_bbl_per_hour']='101'
        self.rows[0]['start_at']='2026-11-03T00:00:00.1+09:00'
        self.assertEqual(self.apply(),(self.pairs,0))

    def test_other_group_load_does_not_disable(self):
        self.snapshot['calls'].append({'call_id':'L','cargo_group':'CRUDE','direction':'LOAD'})
        self.assertEqual(filter_tank_pairs(self.snapshot,self.rows,self.pairs,carried_transfers=[{'call_id':'L'}]),([],1))

    def test_opt_in_solver_returns_independently_valid_allocation(self):
        from src.daily_planning.tank.v3_optimization import solve_v3_allocation
        snapshot=copy.deepcopy(self.snapshot)
        snapshot['calls'][0].update(cargo_quantity_mt='10.1',physical={'bbl_per_mt':'10'},fixed_etb_is_hard=False)
        snapshot['berths']=[{'berth_id':'B','connected_tank_ids':'T,U','allowed_cargo_groups':'PRODUCT','allowed_operations':'DISCHARGE','max_transfer_rate_mt_h':'10.1'}]
        snapshot['tanks'][0].update(cargo_group='PRODUCT',connected_berth_ids=['B'],initial_inventory_bbl='0')
        snapshot['tanks'].append(dict(snapshot['tanks'][0],tank_id='U',capacity_bbl='200'))
        snapshot['fixed_process_flows'].update(inventory_as_of_at=self.start)
        snapshot['fixed_process_flows']['segments'].append(dict(snapshot['fixed_process_flows']['segments'][0],tank_id='U'))
        row=dict(self.rows[0],berth_id='B',eligible_tank_ids=['T','U'])
        result=solve_v3_allocation(snapshot,[row],start_at=self.start,end_at=self.end,
                                   same_tank_policy='ALLOW',capacity_bound_policy='V3_SINGLE_TANK_CAPACITY_BOUND_V1')
        self.assertEqual(result['status'],'VALID',result)
        self.assertEqual(result['assignments'][0]['tank_id'],'U')
        self.assertEqual(result['capacity_bound_removed_pairs'],1)

if __name__=='__main__':
    unittest.main()
