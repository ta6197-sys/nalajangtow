import unittest
from src.daily_planning.physical_scheduling.service import solve_berth_schedule

class SearchHeuristicTest(unittest.TestCase):
    def test_heuristic_orders_without_removing_candidates(self):
        from tests.test_v3_physical_scheduling import scenario
        s,p=scenario();c=s['calls'][0];b=s['berths'][0];c.update(cargo_group='CRUDE',direction='DISCHARGE');b.update(allowed_cargo_groups='CRUDE',allowed_operations='LOAD,DISCHARGE')
        times=['2026-11-03T01:00:00+09:00','2026-11-03T02:00:00+09:00'];kw={'snapshot':s,'as_of_at':'2026-11-03T00:00:00+09:00','candidate_etbs':{'SYN-A':times},'policy':p,'search_heuristic':'CRUDE_INBOUND_FIRST_V1'}
        first=solve_berth_schedule(**kw);self.assertEqual(first['schedule'][0]['etb_at'],times[0]);self.assertEqual(first['solver_objective_scope'],'HEURISTIC_SEARCH_ORDER_NOT_DEMURRAGE')
        second=solve_berth_schedule(**kw,forbidden_schedules=[first['schedule']]);self.assertEqual(second['schedule'][0]['etb_at'],times[1])
        c['direction']='LOAD';third=solve_berth_schedule(**kw);self.assertEqual(third['schedule'][0]['etb_at'],times[1])
