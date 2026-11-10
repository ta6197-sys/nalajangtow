import unittest
from src.daily_planning.physical_scheduling.compatibility import vessel_berth_duration_seconds
from src.daily_planning.physical_scheduling.phases import replay
from src.daily_planning.physical_scheduling.validator import validate_result
from src.daily_planning.integrated_v3.coordinator import validate_candidate

class TransferDurationTest(unittest.TestCase):
    def setUp(self):
        from tests.test_v3_integration_checkpoint import IntegrationCheckpointTest
        fixture=IntegrationCheckpointTest();fixture.setUp();self.s=fixture.s;self.p=fixture.p;self.c=self.s['calls'][0];self.b=self.s['berths'][0]
    def test_vessel_rate_changes_work_and_release(self):
        legacy=replay(self.c,self.b,'2026-11-03T01:00:00+09:00',self.p)
        self.c['transfer_rate_mt_h']='1';self.p['cargo_duration_policy']='VESSEL_BERTH_RATE_V1'
        row=replay(self.c,self.b,'2026-11-03T01:00:00+09:00',self.p)
        self.assertEqual(legacy['work_seconds'],4);self.assertEqual(row['work_seconds'],3600)
        self.assertEqual(row['work_end_at'],'2026-11-03T02:01:00+09:00');self.assertEqual(row['etu_at'],'2026-11-03T02:03:00+09:00')
        self.assertEqual(validate_result(self.s,[row],self.p)['status'],'VALID')
        result=validate_candidate(self.s,[row],policy=self.p,as_of_at='2026-11-03T00:00:00+09:00')
        self.assertEqual(result['status'],'PHYSICAL_AND_TANK_ONLY',result)
        self.assertFalse(result['operational_approval'])
    def test_longer_duration_rechecks_downstream_occupancy(self):
        self.c['transfer_rate_mt_h']='1';self.p['cargo_duration_policy']='VESSEL_BERTH_RATE_V1'
        other=dict(self.c,call_id='SYN-B');self.s['calls'].append(other)
        first=replay(self.c,self.b,'2026-11-03T01:00:00+09:00',self.p)
        second=replay(other,self.b,'2026-11-03T01:30:00+09:00',self.p)
        r=validate_result(self.s,[first,second],self.p)
        self.assertIn('SAME_BERTH_OCCUPANCY',[issue['code'] for issue in r['issues']])
    def test_source_rate_fallback_and_missing_rate(self):
        self.c['transfer_rate_mt_h']='2';self.b['max_transfer_rate_mt_h']=None
        self.assertEqual(vessel_berth_duration_seconds(self.c,self.b),1800)
        self.c['transfer_rate_mt_h']=None
        with self.assertRaises(ValueError):vessel_berth_duration_seconds(self.c,self.b)
    def test_policy_is_opt_in(self):
        self.c['transfer_rate_mt_h']='1';legacy=replay(self.c,self.b,'2026-11-03T01:00:00+09:00',self.p)
        self.assertEqual(legacy['work_seconds'],4)
    def test_cpsat_does_not_use_old_berth_release(self):
        from src.daily_planning.physical_scheduling.service import solve_berth_schedule
        self.c['transfer_rate_mt_h']='1';self.p['cargo_duration_policy']='VESSEL_BERTH_RATE_V1'
        self.s['calls'].append(dict(self.c,call_id='SYN-B'))
        r=solve_berth_schedule(self.s,as_of_at='2026-11-03T00:00:00+09:00',candidate_etbs={'SYN-A':['2026-11-03T01:00:00+09:00'],'SYN-B':['2026-11-03T01:30:00+09:00']},policy=self.p)
        self.assertEqual(r['status'],'INFEASIBLE',r)
