import unittest

from src.daily_planning.risk.tradeoff import compare_candidates
from src.daily_planning.risk.candidate_reader import normalize_db_candidate


def make_candidate(label, costs, waits, starts, berths=('BERTH-01', 'BERTH-02'), snapshot='sha-123'):
    rows = []
    for idx, cid in enumerate(('DEM-0001', 'DEM-0002')):
        rows.append({
            'call_id': cid,
            'berth_id': berths[idx],
            'work_start_at': starts[idx],
            'total_wait_minutes': waits[idx],
            'demurrage_increment_to_completion_cents': costs[idx],
        })
    return {
        'label': label, 'candidate_id': label + '_id',
        'input_snapshot_hash': snapshot, 'solver_status': 'FEASIBLE',
        'cost_cents': sum(costs), 'wait_minutes': sum(waits),
        'vessel_results': rows,
    }


class TradeoffTest(unittest.TestCase):
    def setUp(self):
        self.a = make_candidate('A', [4000000, 6000000], [12000, 6000],
            ['2026-11-05T08:00:00+09:00', '2026-11-05T09:00:00+09:00'])
        self.b = make_candidate('B', [4500000, 5800000], [9000, 6000],
            ['2026-11-05T10:00:00+09:00', '2026-11-05T09:00:00+09:00'],
            ('BERTH-03', 'BERTH-02'))

    def test_tradeoff_and_vessel_sum(self):
        res = compare_candidates([self.a, self.b])
        b = res['comparisons'][1]
        self.assertEqual(b['extra_cost_vs_reference_cents'], 300000)
        self.assertEqual(b['wait_savings_vs_reference_minutes'], 3000)
        self.assertEqual(b['usd_per_wait_hour_saved'], '60.00')
        self.assertEqual(b['berth_change_count_vs_reference'], 1)
        self.assertEqual(b['total_absolute_start_shift_minutes'], 120)
        self.assertEqual(sum(r['cost_delta_cents'] for r in b['vessel_deltas']), 300000)

    def test_a_not_necessarily_lowest(self):
        cheap = make_candidate('B', [3000000, 5500000], [8000, 6000],
            ['2026-11-05T10:00:00+09:00', '2026-11-05T09:00:00+09:00'])
        res = compare_candidates([self.a, cheap])
        self.assertFalse(res['baseline_is_observed_cost_minimum'])
        self.assertEqual(res['observed_lowest_cost_label'], 'B')
        self.assertEqual(res['comparisons'][1]['tradeoff_category'], 'DOMINATES_BASELINE_ON_COST_AND_WAIT')

    def test_snapshot_mismatch(self):
        self.b['input_snapshot_hash'] = 'wrong'
        with self.assertRaises(ValueError):
            compare_candidates([self.a, self.b])

    def test_vessel_mismatch_and_wrong_total(self):
        self.b['vessel_results'].pop()
        with self.assertRaises(ValueError):
            compare_candidates([self.a,self.b])
        self.b = make_candidate('B', [4500000, 5800000], [9000, 6000],
            ['2026-11-05T10:00:00+09:00', '2026-11-05T09:00:00+09:00'])
        self.b['cost_cents'] += 1
        with self.assertRaises(ValueError):
            compare_candidates([self.a,self.b])

    def test_real_schema_adapter(self):
        ev = {
            'evaluation_id': 'uuid-eval', 'case':'BASELINE','status':'SUCCEEDED',
            'input_snapshot_hash':'sha-123',
            'summary':{'demurrage_increment_to_completion_cents':10000000, 'total_wait_minutes':18000},
            'vessel_results':self.a['vessel_results'],
        }
        row = {
            'run_id':'run_id','candidate_id':'candidate_id','candidate_rank':1,
            'strategy':'MIN_DEMURRAGE','simulated_demurrage_cents':10000000,
            'evaluation_metrics':{'evaluations':[ev],'comparison_evaluation_id':'uuid-eval',
                                  'solver':{'solver_status':'FEASIBLE','relative_gap':0.1},
                                  'schedule_signature':'sig', 'freeze_signature':'freeze'},
        }
        normalized = normalize_db_candidate(row,'sha-123')
        self.assertEqual(normalized['label'],'A')
        self.assertEqual(normalized['cost_cents'],10000000)
        self.assertEqual(normalized['wait_minutes'],18000)
        with self.assertRaises(ValueError):
            normalize_db_candidate(row,'wrong')


if __name__ == '__main__':
    unittest.main()
