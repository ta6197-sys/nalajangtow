import copy
import unittest
from src.daily_planning.integrated_v3.synthetic_rolling import run_synthetic_trial, simulated_boundary_state

class SyntheticRollingTest(unittest.TestCase):
    def fixture(self):
        from tests.test_v3_integration_checkpoint import IntegrationCheckpointTest
        f=IntegrationCheckpointTest();f.setUp();c=copy.deepcopy(f.s['calls'][0]);c.update(call_id='SYN-B',eta_at='2026-11-04T01:00:00+09:00');f.s['calls'].append(c)
        return f.s,f.p
    def test_two_day_state_reconciles_and_completed_not_replayed(self):
        s,p=self.fixture();before=copy.deepcopy((s,p))
        r=run_synthetic_trial(s,as_of_at='2026-11-03T00:00:00+09:00',policy=p,days=2,time_limit_per_day=15)
        self.assertEqual(r['status'],'ALL_REQUESTED_DAYS_SUBSYSTEM_VALID',r)
        self.assertEqual(r['steps'][1]['summary']['completed_call_ids'],['SYN-A'])
        self.assertEqual(r['steps'][1]['result']['validation']['tank_result']['inventories']['TEST-TANK'][0]['inventory_bbl'],'90')
        self.assertEqual((s,p),before);self.assertFalse(r['operational_approval'])
    def test_cannot_advance_unsuccessful_prior(self):
        s,p=self.fixture()
        with self.assertRaises(ValueError):simulated_boundary_state(s,{'status':'INVALID'},policy=p)
    def test_finished_cargo_still_locks_berth_without_duplicate_transfer(self):
        s,p=self.fixture();s['calls'][0]['eta_at']='2026-11-03T23:57:00+09:00';s['calls'][1]['eta_at']='2026-11-04T02:00:00+09:00';s['berths'][0]['hose_disconnect_min']='60'
        r=run_synthetic_trial(s,as_of_at='2026-11-03T00:00:00+09:00',policy=p,days=2,time_limit_per_day=15)
        self.assertEqual(r['status'],'ALL_REQUESTED_DAYS_SUBSYSTEM_VALID',r)
        second=r['steps'][1];self.assertIn('SYN-A',second['summary']['protected_call_ids'])
        tank=second['result']['validation']['tank_result'];self.assertNotIn('SYN-A',[v['call_id'] for v in tank['assignments']]);self.assertEqual(tank['inventories']['TEST-TANK'][0]['inventory_bbl'],'90')
