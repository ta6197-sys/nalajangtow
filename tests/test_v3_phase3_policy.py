import unittest
from tests.test_v3_physical_scheduling import scenario
from src.daily_planning.physical_scheduling.user_policy import build_unbounded_synthetic_policy
from src.daily_planning.physical_scheduling.phases import replay
from src.daily_planning.physical_scheduling.validator import validate_result
from src.daily_planning.physical_scheduling.service import solve_berth_schedule

class Phase3PolicyTest(unittest.TestCase):
    def test_unbounded_and_wait(self):
        s,_=scenario();p=build_unbounded_synthetic_policy(s,start_at='2026-11-03T00:00:00+09:00',end_at='2026-11-09T00:00:00+09:00')
        row=replay(s['calls'][0],s['berths'][0],'2026-11-03T02:00:00+09:00',p,unberthing_start_at='2026-11-03T06:00:00+09:00')
        self.assertEqual(row['pob_at'],'2026-11-03T01:00:00+09:00');self.assertEqual(row['etu_at'],'2026-11-03T07:00:00+09:00')
        self.assertEqual(validate_result(s,[row],p)['status'],'VALID')
        result=solve_berth_schedule(s,as_of_at='2026-11-03T00:00:00+09:00',candidate_etbs={'SYN-A':['2026-11-03T02:00:00+09:00']},policy=p)
        self.assertEqual(result['status'],'BERTH_SUBSYSTEM_FEASIBLE',result)
