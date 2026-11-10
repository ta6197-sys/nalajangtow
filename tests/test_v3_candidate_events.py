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
