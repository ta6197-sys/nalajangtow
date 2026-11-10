import copy
import unittest
from src.daily_planning.physical_scheduling.phases import replay
from src.daily_planning.physical_scheduling.service import solve_berth_schedule
from src.daily_planning.integrated_v3.candidates import generate_candidate_etbs

class ReplanningStateTest(unittest.TestCase):
    def setUp(self):
        from tests.test_v3_physical_scheduling import scenario
        self.s,self.p=scenario();self.call=self.s['calls'][0];self.s['calls'].append(dict(self.call,call_id='SYN-B'))
        self.old=replay(self.call,self.s['berths'][0],'2026-11-03T01:00:00+09:00',self.p)
    def test_completed_not_rescheduled(self):
        at='2026-11-03T01:30:00+09:00';old=copy.deepcopy(self.old)
        candidates=generate_candidate_etbs(self.s,as_of_at=at,policy=self.p,completed_rows=[old])
        self.assertNotIn('SYN-A',candidates['candidate_etbs']);self.assertIn('SYN-B',candidates['candidate_etbs'])
        r=solve_berth_schedule(self.s,as_of_at=at,policy=self.p,completed_rows=[old],candidate_etbs=candidates['candidate_etbs'])
        self.assertEqual(r['status'],'BERTH_SUBSYSTEM_FEASIBLE',r);self.assertEqual(r['completed_call_ids'],['SYN-A']);self.assertEqual([x['call_id'] for x in r['schedule']],['SYN-B']);self.assertEqual(old,self.old)
    def test_in_progress_cannot_be_marked_completed(self):
        at='2026-11-03T01:01:00+09:00'
        r=solve_berth_schedule(self.s,as_of_at=at,policy=self.p,completed_rows=[self.old],candidate_etbs={})
        self.assertEqual(r['status'],'INVALID_INPUT');self.assertIn('still occupies',r['issues'][0]['reason'])
    def test_completed_cannot_also_be_locked(self):
        r=solve_berth_schedule(self.s,as_of_at='2026-11-03T01:30:00+09:00',policy=self.p,completed_rows=[self.old],commitments=[self.old],candidate_etbs={})
        self.assertEqual(r['status'],'INVALID_INPUT')
    def test_locked_full_row_is_preserved(self):
        at='2026-11-03T01:01:00+09:00'
        r=solve_berth_schedule(self.s,as_of_at=at,policy=self.p,commitments=[self.old],candidate_etbs={'SYN-B':['2026-11-03T02:00:00+09:00']})
        self.assertEqual(r['status'],'BERTH_SUBSYSTEM_FEASIBLE',r);self.assertIn(self.old,r['schedule'])
    def test_integrated_day_boundary_preserves_active_cargo(self):
        from tests.test_v3_integration_checkpoint import IntegrationCheckpointTest
        from src.daily_planning.integrated_v3.search import search_with_events
        f=IntegrationCheckpointTest();f.setUp();f.s['calls'][0]['transfer_rate_mt_h']='1/24';f.p['cargo_duration_policy']='VESSEL_BERTH_RATE_V1'
        first=search_with_events(f.s,as_of_at='2026-11-03T00:00:00+09:00',policy=f.p,time_limit_seconds=15)
        self.assertEqual(first['status'],'PHYSICAL_AND_TANK_ONLY',first)
        prior=first['validation']['tank_result'];self.assertEqual(len(prior['carried_transfers']),1)
        locked=first['berth_result']['schedule'];definitions=prior['transfer_definitions']
        next_run=search_with_events(f.s,as_of_at=prior['carry_at'],policy=f.p,commitments=locked,initial_inventory=prior['carried_inventory_bbl'],carried_transfers=prior['carried_transfers'],tank_commitments=definitions,time_limit_seconds=15)
        self.assertEqual(next_run['status'],'PHYSICAL_AND_TANK_ONLY',next_run)
        self.assertEqual(next_run['berth_result']['schedule'],locked)
        self.assertEqual(next_run['validation']['tank_result']['transfer_definitions'],definitions)
        self.assertEqual(next_run['validation']['tank_result']['inventories']['TEST-TANK'][0]['inventory_bbl'],prior['carried_inventory_bbl']['TEST-TANK'])
