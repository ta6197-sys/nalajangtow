import copy
import unittest
from src.daily_planning.physical_scheduling.conflict_index import build_conflict_index
from src.daily_planning.physical_scheduling.phases import replay
from src.daily_planning.physical_scheduling.validator import pair_conflicts
from src.daily_planning.physical_scheduling.service import solve_berth_schedule

class ConflictIndexTest(unittest.TestCase):
    def setUp(self):
        from tests.test_v3_physical_scheduling import scenario
        self.s,self.p=scenario();b=self.s['berths'][0];self.s['berths'].append(dict(b,berth_id='B2'));self.s['berths'].append(dict(b,berth_id='B3'))
        self.p['berths']['B2']=copy.deepcopy(self.p['berths'][b['berth_id']]);self.p['berths']['B3']=copy.deepcopy(self.p['berths'][b['berth_id']])
        self.s['workbook_tables']['groups']['rows']=[{'rule_id':'G','rule_type':'SAME_CARGO_NOT_CONCURRENT','berth_ids':b['berth_id']+';B2'}]
        self.s['calls'].append(dict(self.s['calls'][0],call_id='SYN-B'))
    def test_prefilter_never_discards_actual_pair_conflict(self):
        rows=[]
        for call in self.s['calls']:
            for berth in self.s['berths']:
                for etb in ('2026-11-03T01:00:00+09:00','2026-11-03T01:02:00+09:00','2026-11-03T03:00:00+09:00'):
                    rows.append(replay(call,berth,etb,self.p))
        possible=build_conflict_index(self.s,self.p,rows)
        for i,a in enumerate(rows):
            for j in range(i+1,len(rows)):
                if a['call_id']!=rows[j]['call_id'] and pair_conflicts(a,rows[j],self.s,self.p):self.assertTrue(possible(i,j),(a,rows[j]))
    def test_solver_matches_unfiltered_feasibility(self):
        kw={'as_of_at':'2026-11-03T00:00:00+09:00','policy':self.p,'candidate_etbs':{'SYN-A':['2026-11-03T01:00:00+09:00'],'SYN-B':['2026-11-03T01:00:00+09:00']}}
        a=solve_berth_schedule(self.s,**kw);b=solve_berth_schedule(self.s,conflict_filter='SAFE_ENVELOPE_V1',**kw)
        self.assertEqual(a['status'],b['status']);self.assertEqual(a['pair_conflict_count'],b['pair_conflict_count']);self.assertLessEqual(b['pair_checks'],a['pair_checks'])
    def test_cross_berth_4142_and_switching_never_filtered(self):
        a,b=self.s['berths'][:2];self.s['workbook_tables']['groups']['rows']=[{'rule_id':'D','rule_type':'PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP','berth_ids':a['berth_id']+';'+b['berth_id']}]
        self.p.update(dock_4142_version='SYNTHETIC_HOSE_END_V1',dock_4142_berths=[a['berth_id'],b['berth_id']],dock_4142_shared_resources={'HANDLING':['HOSE_CONNECT','CARGO','HOSE_DISCONNECT','SWITCHING']})
        a['switching_min']='180';b['switching_min']='180'
        rows=[]
        for call in self.s['calls']:
            for berth in (a,b):
                for etb in ('2026-11-03T01:00:00+09:00','2026-11-03T02:00:00+09:00','2026-11-03T04:30:00+09:00'):
                    rows.append(replay(call,berth,etb,self.p))
        possible=build_conflict_index(self.s,self.p,rows)
        for i,x in enumerate(rows):
            for j in range(i+1,len(rows)):
                if x['call_id']!=rows[j]['call_id'] and pair_conflicts(x,rows[j],self.s,self.p):self.assertTrue(possible(i,j),(x,rows[j]))
    def test_sweep_equals_direct_conservative_index(self):
        from src.daily_planning.physical_scheduling.conflict_index import iter_potential_pairs
        rows=[replay(call,b,at,self.p) for call in self.s['calls'] for b in self.s['berths'] for at in ('2026-11-03T01:00:00+09:00','2026-11-03T01:02:00+09:00','2026-11-03T03:00:00+09:00')]
        direct=build_conflict_index(self.s,self.p,rows)
        expected={(i,j) for i in range(len(rows)) for j in range(i+1,len(rows)) if direct(i,j)}
        self.assertEqual(set(iter_potential_pairs(self.s,self.p,rows)),expected)
