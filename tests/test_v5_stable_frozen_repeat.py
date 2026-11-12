import unittest
from scripts.v5_stability.stable_frozen_repeat import analyze_records

class StableAuditReportTests(unittest.TestCase):
    def row(self, i, *, ts='2026-11-12T00:00:00+00:00', proof='PROVEN_CANONICAL_SELECTION'):
        return {'iteration':i,'status':'VALIDATED','schedule_tiebreak':{'status':proof},
                'cost_cents':100,'schedule_hash':'same' if ts=='2026-11-12T00:00:00+00:00' else 'changed',
                'candidate_domain_hash':'domain','solver_status':'OPTIMAL','primary_domain_optimality_proven':True,
                'selected_schedule':{'A':{'etb_utc':ts,'berth_id':'BERTH-01'}}}

    def test_proven_repeatability(self):
        r=analyze_records([self.row(1),self.row(2)],2,'domain')
        self.assertEqual(r['status'],'PROVEN_REPEATABLE_IN_FIXED_DOMAIN')
        self.assertEqual(r['max_etb_span_minutes'],0)

    def test_same_cost_different_schedule(self):
        rows=[self.row(1),self.row(2,ts='2026-11-12T02:00:00+00:00')]
        r=analyze_records(rows,2,'domain')
        self.assertEqual(r['status'],'VARIATION_DETECTED')
        self.assertEqual(r['max_etb_span_minutes'],120)

    def test_fallback_never_claims_proven(self):
        rows=[self.row(1),self.row(2,proof='SKIPPED_TIME_BUDGET')]
        r=analyze_records(rows,2,'domain')
        self.assertEqual(r['status'],'OBSERVED_REPEATABLE_BUT_CANONICAL_NOT_PROVEN')

    def test_missing_run_not_hidden(self):
        r=analyze_records([self.row(1)],2,'domain')
        self.assertEqual(r['status'],'INCOMPLETE_OR_FAILED')

    def test_different_candidate_hash_detected(self):
        a,b=self.row(1),self.row(2)
        b['candidate_domain_hash']='other'
        self.assertEqual(analyze_records([a,b],2,'domain')['status'],'VARIATION_DETECTED')

if __name__=='__main__': unittest.main()
