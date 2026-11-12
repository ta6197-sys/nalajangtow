import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.v5_stability import stage2b_budget_audit as audit

from src.daily_planning.integrated_v3.v5_budget_evidence import (
    utc, window_evidence, budget_report, compare_budgets,
)


def win(etb='2026-11-12T10:00:00+09:00', berth='BERTH-01', cost=100, initial='i', final='f', status='OPTIMAL'):
    return {'window_id':1, 'first_domain_hash':initial, 'final_domain_hash':final,
            'schedule_hash':etb + ':' + berth, 'cost_cents':cost,
            'schedule':{'A':{'etb_utc':utc(etb).isoformat(), 'berth_id':berth}},
            'final_domain_optimality_proven':status=='OPTIMAL', 'solver_status':status,
            'stopping_reason':'NO_NEW_CANDIDATES'}


def trial(i, *, budget=60, **kwargs):
    return {'iteration':i,'budget_seconds_per_window':budget,'status':'PASS',
            'structural_valid':True,'replay_status':'PASS',
            'execution_status':'REQUESTED_WINDOWS_VALIDATED',
            'source_fingerprint':'s','policy_fingerprint':'p','scope_call_ids':['A'],
            'as_of_at':'2026-11-12T00:00:00+09:00',
            'all_scope_calls_completed':False,'cumulative_demurrage_cents':None,
            'windows':[win(**kwargs)]}


class BudgetEvidenceTest(unittest.TestCase):
    def test_trial_checks_nested_source_metadata_and_replays(self):
        meta = {'source': {'source_fingerprint': 's', 'scope_call_ids': ['A']},
                'candidates_per_pair': 6, 'total_candidates': 240,
                'improvement_rounds': 2, 'tie_break_mode': 'CANONICAL_ETB_BERTH_V1'}
        manifest = {'config': {'source_fingerprint': 's', 'scope': ['A'],
                               'time_limit_seconds': 60,
                               'tie_break_mode': 'CANONICAL_ETB_BERTH_V1'}}
        checkpoints = [{'validation_results': {'carryover': 'VALID'}} for _ in range(2)]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            def command(argv, *, timeout):
                if 'run_stage.py' in argv[1]:
                    directory = root / 'trials/budget_60/repeat_001/runs/run-1'
                    directory.mkdir(parents=True)
                    (directory / 'execution_report.json').write_text(json.dumps({
                        'status': 'REQUESTED_WINDOWS_VALIDATED', 'source_fingerprint': 's'}))
                    return {'exit_code': 0}
                return {'exit_code': 0, 'stdout_tail': json.dumps({
                    'status': 'REQUESTED_WINDOWS_VALIDATED', 'independent_replay': 'PASS',
                    'validated_window_count': 2})}
            with patch.object(audit, 'command', side_effect=command), \
                 patch.object(audit, 'load_checkpoints', return_value=(manifest, checkpoints)):
                record = audit.trial_run(root, meta, root / 'config.json', budget=60,
                                         iteration=1, execution_timeout=10, replay_timeout=10)
            self.assertEqual(record['status'], 'PASS')
            self.assertEqual(record['replay_status'], 'PASS')

    def test_aware_utc(self):
        self.assertEqual(utc('2026-11-12T10:00:00+09:00'),utc('2026-11-12T01:00:00+00:00'))
    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):utc('2026-11-12T10:00:00')
    def test_identical(self):
        t=[trial(1),trial(2)]
        self.assertEqual(budget_report(t,expected=2,budget=60)['status'],'OBSERVED_REPEATABLE_AT_THIS_BUDGET')
        self.assertEqual(window_evidence(t)[0]['max_etb_span_minutes'],0)
    def test_etb_variation_same_cost(self):
        t=[trial(1),trial(2,etb='2026-11-12T12:00:00+09:00')]
        w=window_evidence(t)[0]
        self.assertIn('SCHEDULE_VARIATION',w['diagnostic_flags'])
        self.assertIn('EQUAL_COST_SCHEDULE_VARIATION_CHECK_SECONDARY_STATUS',w['diagnostic_flags'])
        self.assertEqual(w['max_etb_span_minutes'],120)
    def test_initial_domain_variation(self):
        w=window_evidence([trial(1),trial(2,initial='other')])[0]
        self.assertIn('INITIAL_DOMAIN_VARIATION',w['diagnostic_flags'])
    def test_final_domain_variation(self):
        w=window_evidence([trial(1),trial(2,final='other')])[0]
        self.assertIn('FINAL_DOMAIN_VARIATION',w['diagnostic_flags'])
    def test_cost_variation(self):
        w=window_evidence([trial(1),trial(2,cost=150)])[0]
        self.assertIn('COST_VARIATION',w['diagnostic_flags'])
    def test_finite_proof_missing(self):
        w=window_evidence([trial(1),trial(2,status='FEASIBLE')])[0]
        self.assertIn('FINITE_DOMAIN_OPTIMALITY_NOT_PROVEN_IN_ALL_RUNS',w['warnings'])
    def test_context_change_is_not_success(self):
        x=trial(2);x['policy_fingerprint']='changed'
        self.assertEqual(budget_report([trial(1),x],expected=2,budget=60)['status'],'CONTEXT_MISMATCH')
    def test_missing_replay_is_failure(self):
        x=trial(2);x['replay_status']='FAILED'
        a=budget_report([trial(1),x],expected=2,budget=60)
        self.assertEqual(a['status'],'INCOMPLETE_OR_FAILED')
        self.assertEqual(a['valid_and_replayed'],1)
    def test_missing_iteration_is_failure(self):
        a=budget_report([trial(1)],expected=2,budget=60)
        self.assertEqual(a['status'],'INCOMPLETE_OR_FAILED')
    def test_duplicate_iteration_rejected(self):
        with self.assertRaises(ValueError):budget_report([trial(1),trial(1)],expected=2,budget=60)
    def test_unresolved_cost_rejected(self):
        x=trial(1);x['windows'][0]['cost_cents']=None
        with self.assertRaises(ValueError):window_evidence([x])
    def test_cross_budget(self):
        r=compare_budgets({60:[trial(1)],120:[trial(1,budget=120,etb='2026-11-12T11:00:00+09:00')]})
        self.assertEqual(r['pairs'][0]['windows'][0]['max_etb_delta_minutes'],60)
    def test_cross_budget_different_scope_fail(self):
        y=trial(1,budget=120);y['scope_call_ids']=['B']
        with self.assertRaises(ValueError):compare_budgets({60:[trial(1)],120:[y]})
    def test_single_budget(self):
        self.assertEqual(compare_budgets({60:[]})['status'],'NEEDS_AT_LEAST_TWO_BUDGETS')


if __name__=='__main__':unittest.main()
