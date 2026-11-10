import unittest
from unittest.mock import patch
from tests.test_v3_physical_scheduling import scenario
from src.daily_planning.physical_scheduling.service import solve_berth_schedule
from src.daily_planning.integrated_v3.search import search_feasibility

START='2026-11-03T00:00:00+09:00'
TIMES=['2026-11-03T01:00:00+09:00','2026-11-03T02:00:00+09:00']

class IntegratedSearchTest(unittest.TestCase):
    def setUp(self):self.s,self.p=scenario()
    def berth(self,**kwargs):return solve_berth_schedule(self.s,as_of_at=START,candidate_etbs={'SYN-A':TIMES},policy=self.p,**kwargs)
    def search(self):return search_feasibility(self.s,as_of_at=START,candidate_etbs={'SYN-A':TIMES},policy=self.p)
    def test_exact_complete_assignment_exclusion(self):
        first=self.berth();self.assertEqual(first['status'],'BERTH_SUBSYSTEM_FEASIBLE')
        second=self.berth(forbidden_schedules=[first['schedule']]);self.assertEqual(second['status'],'BERTH_SUBSYSTEM_FEASIBLE');self.assertNotEqual(first['schedule'][0]['etb_at'],second['schedule'][0]['etb_at'])
        last=self.berth(forbidden_schedules=[first['schedule'],second['schedule']]);self.assertEqual(last['status'],'INFEASIBLE')
    def test_verified_infeasible_retries(self):
        fail={'status':'TANK_SUBPROBLEM_NOT_VALIDATED','tank_result':{'status':'INFEASIBLE'}}
        passed={'status':'PHYSICAL_AND_TANK_ONLY'}
        with patch('src.daily_planning.integrated_v3.search.validate_candidate',side_effect=[fail,passed]) as check:
            r=self.search()
        self.assertEqual(r['status'],'PHYSICAL_AND_TANK_ONLY',r);self.assertEqual(len(r['iterations']),2);self.assertEqual(check.call_count,2);self.assertFalse(r['operational_approval'])
    def test_timeout_is_not_nogood(self):
        fail={'status':'TANK_SUBPROBLEM_NOT_VALIDATED','tank_result':{'status':'TIME_LIMIT_NO_INCUMBENT'}}
        with patch('src.daily_planning.integrated_v3.search.validate_candidate',return_value=fail) as check:r=self.search()
        self.assertEqual(r['status'],'UNRESOLVED_VALIDATION');self.assertEqual(check.call_count,1)
    def test_invalid_budget(self):
        r=search_feasibility(self.s,as_of_at=START,candidate_etbs={},policy=self.p,time_limit_seconds=0)
        self.assertEqual(r['status'],'INVALID_INPUT')

    def test_real_inventory_feedback_reaches_later_candidate(self):
        from tests.test_v3_integration_checkpoint import IntegrationCheckpointTest
        fixture=IntegrationCheckpointTest();fixture.setUp()
        fixture.s['tanks'][0].update(initial_inventory_bbl='94',capacity_bbl='100')
        first=fixture.s['fixed_process_flows']['segments'][0]
        first['end_at']='2026-11-03T02:00:00+09:00';first['fixed_net_flow_bbl_per_hour']='5'
        fixture.s['fixed_process_flows']['segments'].append(dict(first,start_at='2026-11-03T02:00:00+09:00',end_at='2026-11-08T00:00:00+09:00',fixed_net_flow_bbl_per_hour='0'))
        r=search_feasibility(fixture.s,as_of_at=START,candidate_etbs={'SYN-A':TIMES},policy=fixture.p,time_limit_seconds=15)
        self.assertEqual(r['status'],'PHYSICAL_AND_TANK_ONLY',r)
        self.assertEqual(r['berth_result']['schedule'][0]['etb_at'],TIMES[0])
        self.assertEqual([entry['tank_status'] for entry in r['iterations']],['INFEASIBLE','VALID'])
