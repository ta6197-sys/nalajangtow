import copy
import unittest
from tests.test_v3_physical_scheduling import scenario
from src.daily_planning.physical_scheduling.phases import replay
from src.daily_planning.integrated_v3.coordinator import validate_candidate

class IntegrationCheckpointTest(unittest.TestCase):
    def setUp(self):
        self.s,self.p=scenario();c=self.s['calls'][0];b=self.s['berths'][0]
        c['cargo_volume_bbl']='10';c['physical']['bbl_per_mt']='10'
        b['connected_tank_ids']='TEST-TANK';b['allowed_operations']=c['direction'];b['allowed_cargo_groups']=c['cargo_group']
        self.s['tanks']=[{'tank_id':'TEST-TANK','connected_berth_ids':[b['berth_id']],'cargo_group':c['cargo_group'],'initial_inventory_bbl':'100','min_inventory_bbl':'0','capacity_bbl':'1000'}]
        self.s['fixed_process_flows']={'inventory_as_of_at':'2026-11-03T00:00:00+09:00','segments':[{'tank_id':'TEST-TANK','start_at':'2026-11-03T00:00:00+09:00','end_at':'2026-11-08T00:00:00+09:00','fixed_net_flow_bbl_per_hour':'0'}]}
        self.row=replay(c,b,'2026-11-03T01:00:00+09:00',self.p)
    def validate(self,rows):return validate_candidate(self.s,rows,policy=self.p,as_of_at='2026-11-03T00:00:00+09:00')
    def test_integration_is_not_approval(self):
        before=copy.deepcopy((self.s,self.p,self.row));r=self.validate([self.row])
        self.assertEqual(r['status'],'PHYSICAL_AND_TANK_ONLY',r);self.assertFalse(r['operational_approval']);self.assertIn('CONTRACT_SETTLEMENT',r['pending_checks']);self.assertEqual(before,(self.s,self.p,self.row))
    def test_invalid_physical_replay_rejected(self):
        self.row['etu_at']='2026-11-03T01:00:00+09:00';self.assertEqual(self.validate([self.row])['status'],'PHYSICAL_VALIDATION_FAILED')
    def test_missing_connection_rejected(self):
        self.s['tanks'][0]['connected_berth_ids']=[];self.assertEqual(self.validate([self.row])['status'],'INVALID_INPUT')
    def test_tank_inventory_failure_rejected(self):
        self.s['tanks'][0]['capacity_bbl']='100';self.s['calls'][0]['direction']='DISCHARGE';self.s['berths'][0]['allowed_operations']='DISCHARGE'
        self.row=replay(self.s['calls'][0],self.s['berths'][0],self.row['etb_at'],self.p)
        self.assertEqual(self.validate([self.row])['status'],'TANK_SUBPROBLEM_NOT_VALIDATED')
