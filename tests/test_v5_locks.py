import unittest
from src.daily_planning.integrated_v3.v5_locks import apply_overrides,resolve_locks

START='2026-11-03T00:00:00+09:00'
class DynamicLockTests(unittest.TestCase):
    def setUp(self):self.call={'call_id':'A','eta_at':'2026-11-03T05:00:00+09:00','fixed_etb_is_hard':False};self.row={'call_id':'A','berth_id':'B','berthing_start_at':'2026-11-03T06:00:00+09:00','etb_at':'2026-11-03T07:00:00+09:00'}
    def resolve(self,**kw):return resolve_locks([self.call],as_of_at=START,**kw)
    def test_five_hours_locks(self):self.assertEqual(self.resolve(previous_schedule=[self.row])['decisions']['A']['reason'],'ETA_WITHIN_5_HOURS')
    def test_more_than_five_hours_changeable(self):
        self.call['eta_at']='2026-11-03T05:00:01+09:00';self.assertEqual(self.resolve(previous_schedule=[self.row])['decisions']['A']['status'],'CHANGEABLE')
    def test_missing_prior_schedule_blocks(self):self.assertEqual(self.resolve()['issues'][0]['code'],'LOCK_REQUIRES_EXISTING_SCHEDULE')
    def test_manual_unlock_persists_until_explicit_change(self):
        state=apply_overrides({}, {'A':'MANUAL_UNLOCK'}, {'A'});r=self.resolve(lock_state=state);self.assertEqual(r['decisions']['A']['status'],'CHANGEABLE');self.assertEqual(self.resolve(lock_state=r['lock_state'])['decisions']['A']['mode'],'MANUAL_UNLOCK');self.assertEqual(apply_overrides(state,{'A':'AUTO'},{'A'})['A']['mode'],'AUTO')
    def test_manual_lock_requires_schedule(self):
        self.call['eta_at']='2026-11-03T08:00:00+09:00';r=self.resolve(lock_state={'A':{'mode':'MANUAL_LOCK'}});self.assertEqual(r['issues'][0]['reason'],'MANUAL_LOCK')
    def test_started_not_unlocked(self):
        self.row['berthing_start_at']=START;r=self.resolve(previous_schedule=[self.row],lock_state={'A':{'mode':'MANUAL_UNLOCK'}});self.assertEqual(r['decisions']['A']['reason'],'STARTED')
    def test_approved_commitment_not_unlocked(self):
        r=self.resolve(approved_commitments=[self.row],lock_state={'A':{'mode':'MANUAL_UNLOCK'}});self.assertEqual(r['decisions']['A']['origin'],'OPERATOR_APPROVED_COMMITMENT')
    def test_fix_remains_marked_with_manual_unlock(self):
        self.call['fixed_etb_is_hard']=True;r=self.resolve(lock_state={'A':{'mode':'MANUAL_UNLOCK'}});self.assertTrue(r['decisions']['A']['hard_fix_etb_preserved']);self.assertEqual(r['decisions']['A']['status'],'CHANGEABLE')
if __name__=='__main__':unittest.main()
