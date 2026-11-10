import copy
import unittest
from src.daily_planning.integrated_v3.candidates import generate_candidate_etbs
from src.daily_planning.integrated_v3.search import search_with_events
from src.daily_planning.physical_scheduling.phases import replay
from tests.test_v3_physical_scheduling import scenario

START='2026-11-03T00:00:00+09:00'

class CandidateEventsTest(unittest.TestCase):
    def setUp(self):self.s,self.p=scenario()
    def generate(self,**kw):return generate_candidate_etbs(self.s,as_of_at=START,policy=self.p,**kw)
    def test_availability_and_previous_provenance(self):
        previous=[{'call_id':'SYN-A','etb_at':'2026-11-03T02:00:13+09:00'}];before=copy.deepcopy((self.s,self.p,previous))
        r=self.generate(previous_schedule=previous);values=r['candidate_etbs']['SYN-A'];self.assertIn('2026-11-03T00:01:00+09:00',values);self.assertIn('2026-11-03T02:00:13+09:00',values);self.assertEqual((self.s,self.p,previous),before)
        self.assertIn('PREVIOUS_TENTATIVE_ETB',r['anchors']['SYN-A'][-1]['sources']);self.assertFalse(r['operational_approval'])
    def test_fix_is_the_only_anchor(self):
        self.s['calls'][0].update(fixed_etb_is_hard=True,fix_etb_at='2026-11-03T04:00:00+09:00')
        r=self.generate(previous_schedule=[{'call_id':'SYN-A','etb_at':'2026-11-03T02:00:00+09:00'}]);self.assertEqual(r['candidate_etbs']['SYN-A'],['2026-11-03T04:00:00+09:00'])
    def test_committed_clearance_produces_anchor(self):
        c=self.s['calls'][0];self.s['calls'].append(dict(c,call_id='SYN-B'))
        row=replay(c,self.s['berths'][0],'2026-11-03T01:00:00+09:00',self.p)
        r=self.generate(commitments=[row]);sources=[source for v in r['anchors']['SYN-B'] for source in v['sources']]
        self.assertTrue(any(v.startswith('COMMITTED_CLEARANCE:') for v in sources));self.assertNotIn('SYN-A',r['candidate_etbs'])
    def test_unknown_previous_rejected(self):
        with self.assertRaises(ValueError):self.generate(previous_schedule=[{'call_id':'UNKNOWN','etb_at':START}])
    def test_full_small_fixture_event_search(self):
        from tests.test_v3_integration_checkpoint import IntegrationCheckpointTest
        f=IntegrationCheckpointTest();f.setUp()
        r=search_with_events(f.s,as_of_at=START,policy=f.p,time_limit_seconds=15)
        self.assertEqual(r['status'],'PHYSICAL_AND_TANK_ONLY',r);self.assertIn('candidate_generation',r);self.assertFalse(r['operational_approval'])
    def test_flow_boundaries_are_optional_audited_candidates(self):
        self.s['fixed_process_flows']={'segments':[{'start_at':'2026-11-03T06:00:00+09:00','end_at':'2026-11-03T12:00:00+09:00'}]}
        old=self.generate();r=self.generate(event_policy='V3_ETB_FLOW_EVENTS_V2')
        self.assertNotIn('2026-11-03T06:00:00+09:00',old['candidate_etbs']['SYN-A'])
        self.assertIn('2026-11-03T06:00:00+09:00',r['candidate_etbs']['SYN-A'])
        row=next(a for a in r['anchors']['SYN-A'] if a['etb_at']=='2026-11-03T06:00:00+09:00')
        self.assertIn('FIXED_PROCESS_FLOW_BOUNDARY',row['sources'])
        self.assertFalse(r['operational_approval'])
    def test_flow_events_do_not_move_fix(self):
        self.s['fixed_process_flows']={'segments':[{'start_at':'2026-11-03T06:00:00+09:00','end_at':'2026-11-03T12:00:00+09:00'}]}
        self.s['calls'][0].update(fixed_etb_is_hard=True,fix_etb_at='2026-11-03T04:00:00+09:00')
        r=self.generate(event_policy='V3_ETB_FLOW_EVENTS_V2')
        self.assertEqual(r['candidate_etbs']['SYN-A'],['2026-11-03T04:00:00+09:00'])
    def test_tentative_release_generates_new_feasible_sequence(self):
        from src.daily_planning.physical_scheduling.service import solve_berth_schedule
        self.s['calls'].append(dict(self.s['calls'][0],call_id='SYN-B'))
        self.s['fixed_process_flows']={'segments':[]}
        old=self.generate();new=self.generate(event_policy='V3_ETB_TENTATIVE_RELEASES_V3')
        self.assertTrue(any(source.startswith('TENTATIVE_BERTH_CLEARANCE:') for row in new['anchors']['SYN-B'] for source in row['sources']))
        self.assertGreater(len(new['candidate_etbs']['SYN-B']),len(old['candidate_etbs']['SYN-B']))
        failed=solve_berth_schedule(self.s,as_of_at=START,policy=self.p,candidate_etbs=old['candidate_etbs'])
        solved=solve_berth_schedule(self.s,as_of_at=START,policy=self.p,candidate_etbs=new['candidate_etbs'])
        self.assertEqual(failed['status'],'INFEASIBLE',failed);self.assertEqual(solved['status'],'BERTH_SUBSYSTEM_FEASIBLE',solved)
    def test_tentative_release_does_not_move_fix(self):
        self.s['calls'].append(dict(self.s['calls'][0],call_id='SYN-B',fixed_etb_is_hard=True,fix_etb_at='2026-11-03T04:00:00+09:00'))
        self.s['fixed_process_flows']={'segments':[]}
        r=self.generate(event_policy='V3_ETB_TENTATIVE_RELEASES_V3')
        self.assertEqual(r['candidate_etbs']['SYN-B'],['2026-11-03T04:00:00+09:00'])
    def test_candidate_deadline_does_not_claim_infeasibility(self):
        from unittest.mock import patch
        with patch('src.daily_planning.integrated_v3.candidates.time.monotonic',return_value=2):
            with self.assertRaises(TimeoutError):self.generate(deadline=1)
    def test_physical_preprocessing_obeys_budget(self):
        from src.daily_planning.physical_scheduling.service import solve_berth_schedule
        r=solve_berth_schedule(self.s,as_of_at=START,policy=self.p,candidate_etbs={'SYN-A':['2026-11-03T01:00:00+09:00']},time_limit_seconds=1e-12)
        self.assertEqual(r['status'],'TIME_LIMIT_NO_INCUMBENT',r);self.assertEqual(r['issues'][0]['code'],'TIME_BUDGET_EXHAUSTED')
