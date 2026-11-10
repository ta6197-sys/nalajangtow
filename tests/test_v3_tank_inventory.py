import copy
import unittest
from src.daily_planning.tank.v3_inventory import validate_v3_inventory

class V3InventoryTests(unittest.TestCase):
    def setUp(self):
        self.s={'calls':[{'call_id':'C','cargo_group':'X','direction':'DISCHARGE','cargo_quantity_mt':'10','cargo_volume_bbl':'100','physical':{'bbl_per_mt':'10'},'fixed_etb_is_hard':False}], 'berths':[{'berth_id':'B','connected_tank_ids':'T','allowed_cargo_groups':'X','allowed_operations':'DISCHARGE','max_transfer_rate_mt_h':'10'}], 'tanks':[{'tank_id':'T','cargo_group':'X','connected_berth_ids':['B'],'initial_inventory_bbl':'100','min_inventory_bbl':'0','capacity_bbl':'300'}], 'fixed_process_flows':{'inventory_as_of_at':'2026-11-03T00:00:00+09:00','segments':[{'tank_id':'T','start_at':'2026-11-03T00:00:00+09:00','end_at':'2026-11-03T06:00:00+09:00','fixed_net_flow_bbl_per_hour':'-10'},{'tank_id':'T','start_at':'2026-11-03T06:00:00+09:00','end_at':'2026-11-05T00:00:00+09:00','fixed_net_flow_bbl_per_hour':'0'}]}}
        self.x=[{'call_id':'C','berth_id':'B','tank_id':'T','start_at':'2026-11-03T05:30:01+09:00','end_at':'2026-11-03T06:30:01+09:00'}]
    def run_projection(self):return validate_v3_inventory(self.s,self.x,start_at='2026-11-03T00:00:00+09:00',end_at='2026-11-04T01:00:00+09:00')
    def test_crossing_and_carry(self):
        original=copy.deepcopy((self.s,self.x));r=self.run_projection();self.assertEqual(r['status'],'VALID');self.assertEqual(r['carried_inventory_bbl']['T'],'140');self.assertEqual(r['external_net_flow_totals_bbl']['T'],'-60');self.assertEqual((self.s,self.x),original)
    def test_overflow(self):
        self.s['tanks'][0]['capacity_bbl']='120';self.assertEqual(self.run_projection()['status'],'INVALID')
    def test_flow_gap(self):
        self.s['fixed_process_flows']['segments'][1]['start_at']='2026-11-03T06:00:01+09:00';self.assertEqual(self.run_projection()['status'],'INVALID_INPUT')
    def test_missing_rate(self):
        self.s['berths'][0]['max_transfer_rate_mt_h']=None;self.assertEqual(self.run_projection()['status'],'INVALID_INPUT')
    def test_cargo_mismatch(self):
        self.x[0]['end_at']='2026-11-03T06:30:02+09:00';self.assertEqual(self.run_projection()['status'],'INVALID_INPUT')
    def test_missing_connection(self):
        self.s['tanks'][0]['connected_berth_ids']=[];self.assertEqual(self.run_projection()['status'],'INVALID_INPUT')

    def test_select_tank_milp(self):
        from src.daily_planning.tank.v3_optimization import solve_v3_allocation
        self.s['tanks'].append(dict(self.s['tanks'][0],tank_id='U',capacity_bbl='300'))
        self.s['tanks'][0]['capacity_bbl']='120'
        self.s['berths'][0]['connected_tank_ids']='T,U'
        self.s['fixed_process_flows']['segments'] += [dict(f,tank_id='U') for f in self.s['fixed_process_flows']['segments']]
        x=dict(self.x[0]);x.pop('tank_id');x['eligible_tank_ids']=['T','U']
        r=solve_v3_allocation(self.s,[x],start_at='2026-11-03T00:00:00+09:00',end_at='2026-11-04T01:00:00+09:00')
        self.assertEqual(r['status'],'VALID',r);self.assertEqual(r['assignments'][0]['tank_id'],'U')
