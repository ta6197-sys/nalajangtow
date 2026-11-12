"""Pure evidence-analysis tests; do not require original V5 files or OR-Tools."""
import copy
import unittest
from datetime import datetime, timedelta, timezone
from src.daily_planning.integrated_v3.v5_repeatability import (
    VERSION, digest, read_window, read_run, compare_runs, compare_series, iso_utc,
)


def window(etb='2026-11-10T10:00:00+09:00', cost=0, init='init', final='final',
           berth='BERTH-01', proven=True, wid=1):
    return {
        'window_id': wid, 'as_of_at': '2026-11-10T00:00:00+09:00',
        'selected_schedule': [{'call_id':'DEM-001','berth_id':berth,'etb_at':etb,
            'hose_connection_start_at':'2026-11-10T10:00:00+09:00',
            'hose_connection_end_at':'2026-11-10T11:00:00+09:00',
            'work_start_at':'2026-11-10T11:00:00+09:00',
            'work_end_at':'2026-11-10T12:00:00+09:00',
            'work_intervals':[['2026-11-10T11:00:00+09:00','2026-11-10T12:00:00+09:00']],
            'etu_at':'2026-11-10T14:00:00+09:00'}],
        'summary': {'window_demurrage_cents':cost,'solver_status':'OPTIMAL' if proven else 'FEASIBLE'},
        'candidate': {'optimizer_result': {'initial_candidate_count':4,'final_candidate_count':5,
             'stopping_reason':'IMPROVEMENT_ROUND_LIMIT',
             'cost_improvement_history':[{'round':0,'candidate_domain_fingerprint':init,
                                          'candidate_count':4,'solver_status':'OPTIMAL','validated_cost_cents':cost}],
             'final_candidate_domain': {'candidate_domain_fingerprint':final,'optimality_proven':proven}}},
    }


def run(rid, *, etb='2026-11-10T10:00:00+09:00', cost=0, init='init', final='final',
        berth='BERTH-01', proven=True):
    report={'run_id':rid,'status':'REQUESTED_WINDOWS_VALIDATED','source_fingerprint':'same-source',
            'scope_call_ids':['DEM-001','DEM-002','DEM-003','DEM-004','DEM-005'],
            'requested_windows':2,'validated_window_count':2,'all_scope_calls_completed':False,
            'cumulative_demurrage_cents':None,'unresolved_accounting_call_ids':['DEM-002']}
    return read_run(report,[window(etb,cost,init,final,berth,proven,wid=1),
                             window(etb,cost,init,final,berth,proven,wid=2)],
                    structural_valid=True,replay_status='PASS')


class RepeatabilityTests(unittest.TestCase):
    def test_physical_phase_variations_separate_from_etb_berth(self):
        for field, value in [('hose_connection_start_at','2026-11-10T10:30:00+09:00'),
                             ('work_intervals', [['2026-11-10T11:15:00+09:00','2026-11-10T12:00:00+09:00']]),
                             ('etu_at','2026-11-10T14:30:00+09:00')]:
            with self.subTest(field=field):
                first, second = window(), window()
                second['selected_schedule'][0][field] = value
                a, b = read_window(first), read_window(second)
                self.assertEqual(a['schedule_hash'], b['schedule_hash'])
                self.assertNotEqual(a['physical_schedule_hash'], b['physical_schedule_hash'])
                runs=[run('a'),run('b')]; runs[0]['windows'][0]=a; runs[1]['windows'][0]=b
                self.assertIn('PHYSICAL_SCHEDULE_DIFFERENCE', compare_runs(runs,expected_repetitions=2)['windows'][0]['diagnostic_flags'])

    def test_physical_timezone_equivalence_and_missing(self):
        first, second = window(), window()
        row = second['selected_schedule'][0]
        for field in ('etb_at','hose_connection_start_at','hose_connection_end_at','work_start_at','work_end_at','etu_at'):
            row[field] = datetime.fromisoformat(row[field]).astimezone(timezone.utc).isoformat()
        row['work_intervals'] = [[datetime.fromisoformat(t).astimezone(timezone.utc).isoformat() for t in pair] for pair in row['work_intervals']]
        self.assertEqual(read_window(first)['physical_schedule_hash'], read_window(second)['physical_schedule_hash'])
        del row['etu_at']
        self.assertIsNone(read_window(second)['physical_schedule_hash'])

    def test_interval_order_and_missing_physical_are_not_silently_identical(self):
        first, second = window(), window()
        intervals = [['2026-11-10T11:00:00+09:00','2026-11-10T11:30:00+09:00'],
                     ['2026-11-10T11:45:00+09:00','2026-11-10T12:00:00+09:00']]
        first['selected_schedule'][0]['work_intervals'] = intervals
        second['selected_schedule'][0]['work_intervals'] = list(reversed(intervals))
        self.assertNotEqual(read_window(first)['physical_schedule_hash'],read_window(second)['physical_schedule_hash'])
        runs=[run(str(i)) for i in range(20)]
        runs[0]['windows'][0]['physical_schedule_hash']=None
        result=compare_runs(runs)
        self.assertEqual(result['status'],'INCOMPLETE_OR_UNREPLAYED')
        self.assertEqual(result['windows'][0]['missing_physical_evidence_count'],1)

    def test_missing_domains_among_twenty_and_all(self):
        runs=[run(str(i)) for i in range(20)]
        runs[0]['windows'][0]['first_domain_hash']=None
        result=compare_runs(runs)
        self.assertEqual(result['status'],'INCOMPLETE_OR_UNREPLAYED')
        self.assertEqual(result['windows'][0]['missing_initial_domain_count'],1)
        self.assertIn('MISSING_DOMAIN_EVIDENCE',result['windows'][0]['diagnostic_flags'])
        for item in runs: item['windows'][0]['final_domain_hash']=None
        self.assertEqual(compare_runs(runs)['windows'][0]['missing_final_domain_count'],20)

    def test_utc_normalization(self):
        self.assertEqual(iso_utc('2026-11-10T10:00:00+09:00'), iso_utc('2026-11-10T01:00:00Z'))
        self.assertNotEqual(iso_utc('2026-11-10T10:00:00.000001+09:00'), iso_utc('2026-11-10T10:00:00Z'))
        with self.assertRaises(ValueError):iso_utc('2026-11-10T10:00:00')

    def test_complete_20_identical(self):
        runs=[run('v5-%02d'%i) for i in range(20)]
        report=compare_runs(runs)
        self.assertEqual(report['status'],'REPEATABLE_IN_TESTED_WINDOWS')
        self.assertEqual(report['validated_and_replayed'],20)
        self.assertEqual(len(report['windows']),2)
        self.assertFalse(runs[0]['all_scope_calls_completed'])
        self.assertIsNone(runs[0]['cumulative_demurrage_cents'])
        self.assertEqual(report['windows'][0]['max_etb_span_minutes'],0)

    def test_two_hour_etb_variation_same_cost_same_domain(self):
        runs=[run('v5-%02d'%i,etb=('2026-11-10T12:00:00+09:00' if i==19 else '2026-11-10T10:00:00+09:00')) for i in range(20)]
        out=compare_runs(runs)
        self.assertEqual(out['status'],'VARIATION_DETECTED')
        self.assertEqual(out['windows'][0]['max_etb_span_minutes'],120)
        self.assertIn('POSSIBLE_TIE_IF_ALL_SOLVES_PROVEN',out['windows'][0]['diagnostic_flags'])

    def test_domain_variation_isolated(self):
        runs=[run('v5-%02d'%i,init='another-initial' if i==3 else 'init',final='another-final' if i==3 else 'final') for i in range(20)]
        out=compare_runs(runs)
        self.assertIn('INITIAL_DOMAIN_DIFFERENCE',out['windows'][0]['diagnostic_flags'])
        self.assertIn('EXPANDED_DOMAIN_DIFFERENCE',out['windows'][0]['diagnostic_flags'])

    def test_cost_variation_cannot_claim_same_optimum(self):
        runs=[run('v5-%02d'%i,cost=100 if i==2 else 80,proven=False) for i in range(20)]
        out=compare_runs(runs)
        self.assertEqual(out['windows'][0]['cost_cents_min_max'],[80,100])
        self.assertIn('COST_DIFFERENCE',out['windows'][0]['diagnostic_flags'])
        self.assertFalse(out['windows'][0]['all_final_domains_optimal_proven'])

    def test_berth_changes_are_counted(self):
        runs=[run('v5-%02d'%i,berth='BERTH-02' if i==1 else 'BERTH-01') for i in range(20)]
        out=compare_runs(runs)
        self.assertTrue(out['windows'][0]['vessels'][0]['berth_variation'])
        self.assertEqual(out['windows'][0]['vessels'][0]['reference_berth_changes'],1)

    def test_missing_replay_prevents_false_success(self):
        runs=[run('v5-%02d'%i) for i in range(20)]
        runs[10]['independent_replay']='FAIL'
        out=compare_runs(runs)
        self.assertEqual(out['status'],'INCOMPLETE_OR_UNREPLAYED')
        self.assertEqual(out['validated_and_replayed'],19)

    def test_missing_window_prevents_false_success(self):
        runs=[run('v5-%02d'%i) for i in range(20)]
        runs[10]['validated_window_count']=1
        runs[10]['fully_validated_windows']=False
        self.assertEqual(compare_runs(runs)['status'],'INCOMPLETE_OR_UNREPLAYED')

    def test_null_window_cost_rejected(self):
        cp=window();cp['summary']['window_demurrage_cents']=None
        with self.assertRaisesRegex(ValueError,'unresolved window cost'):read_window(cp)

    def test_duplicate_call_rejected(self):
        cp=window();cp['selected_schedule'].append(copy.deepcopy(cp['selected_schedule'][0]))
        with self.assertRaisesRegex(ValueError,'Duplicate call'):read_window(cp)

    def test_a_b_series_respects_missing_and_variation(self):
        arr=[{'status':'PASS','domain_hash':'a','cost_cents':0,'schedule_hash':'s'} for _ in range(20)]
        self.assertEqual(compare_series('B',arr)['status'],'REPEATED')
        arr[8]['domain_hash']='b'
        self.assertEqual(compare_series('B',arr)['status'],'VARIATION_DETECTED')
        arr[8]['status']='FAIL'
        self.assertEqual(compare_series('B',arr)['status'],'INCOMPLETE')

    def test_empty_window_without_domains_is_not_false_failure(self):
        runs=[]
        for i in range(20):
            r=run('v5-%02d'%i)
            empty=window(wid=2)
            empty['selected_schedule']=[]
            empty['candidate']=None
            empty['summary']['window_demurrage_cents']=0
            r['windows'][1]=read_window(empty)
            runs.append(r)
        self.assertEqual(compare_runs(runs)['status'],'REPEATABLE_IN_TESTED_WINDOWS')

    def test_policy_fingerprint_mismatch_is_rejected(self):
        runs=[run('v5-%02d'%i) for i in range(20)]
        runs[2]['policy_fingerprint']='new-policy'
        out=compare_runs(runs)
        self.assertEqual(out['status'],'INCOMPLETE_OR_UNREPLAYED')
        self.assertTrue(any(x['code']=='EFFECTIVE_POLICY_FINGERPRINT_CHANGED' for x in out['issues']))

    def test_read_window_preserves_microseconds(self):
        cp=window(etb='2026-11-10T10:00:00.123456+09:00')
        self.assertEqual(read_window(cp)['schedule']['DEM-001']['etb_utc'], '2026-11-10T01:00:00.123456+00:00')


if __name__=='__main__':unittest.main()
