"""Unit tests for the read-only Stage 4 checkpoint-chain audit.

These are structural fixture tests; physical/contract replay tests are in
existing tests.test_v5_integrated_rolling.
"""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from src.daily_planning.integrated_v3.v5_rolling_audit import _fingerprint, audit_rolling_run

START = datetime(2026, 11, 3, tzinfo=timezone(timedelta(hours=9)))


def _iso(dt):
    return dt.isoformat()


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')


class V5RollingCheckpointAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = {
            'start_at': _iso(START),
            'source_fingerprint': 'source-fixture',
            'policy_fingerprint': 'policy-fixture',
            'rolling_optimizer_mode': 'INTEGRATED_COST_AWARE_V1',
            'objective_mode': 'MIN_TOTAL_DEMURRAGE',
            'inventory_constraint_mode': 'EXACT_EVENT_BALANCE_V1',
            'candidate_generation_mode': 'AUTO_ADAPTIVE_CANDIDATES',
            'candidate_improvement_mode': 'COST_AWARE_V1',
            'windows': 3,
        }
        self.run_fp = _fingerprint(self.config)
        _write(self.root/'manifest.json', {'config': self.config, 'run_fingerprint': self.run_fp,
                                           'operational_approval': False})
        self.add_window(1)
        self.add_window(2)

    def add_window(self, i, *, cost=1000):
        at = START + timedelta(hours=24*(i-1))
        end = at + timedelta(hours=24)
        previous_state = None
        if i > 1:
            previous_state = json.loads((self.root / f'window_{i-1:03d}.json').read_text())['next_state']
        row = {'call_id': 'SYN-1', 'etb_at': _iso(at+timedelta(hours=5))}
        completed_rows = [row] if i > 1 else []
        progress = {'SYN-1': {'completed_bbl': '100' if i>1 else '60',
                              'remaining_bbl': '0' if i>1 else '40'}}
        state = {'at': _iso(end), 'inventory': {'T': '500'}, 'carried_transfers': [],
                 'cargo_progress': progress, 'contract_accounting_state': {},
                 'in_progress_contract_accounting': {}, 'completed_rows': completed_rows}
        domain_fp = 'domain-' + str(i)
        final = {'candidate_domain_fingerprint': domain_fp, 'optimality_proven': True,
                 'solver_status': 'OPTIMAL', 'best_objective_bound_cents': cost, 'optimality_gap': 0}
        result = {'final_candidate_domain': final, 'best_validated_incumbent': {'total_demurrage_cents': cost},
                  'candidate_domain_fingerprint': domain_fp, 'final_domain_optimality_proven': True,
                  'optimization_status': 'OPTIMAL'}
        candidate = {'selected_schedule': [row], 'total_demurrage_cents': cost, 'optimizer_result': result}
        summary = {'window_id': i, 'as_of_at': _iso(at), 'window_demurrage_cents': cost,
                   'best_demurrage_cents': cost, 'solver_status': 'OPTIMAL',
                   'final_domain_optimality_proven': True}
        cp = {'window_id': i, 'as_of_at': _iso(at), 'planning_horizon_end': _iso(at+timedelta(hours=72)),
              'freeze_boundary': None, 'dynamic_lock_horizon_hours': 5,
              'run_fingerprint': self.run_fp, 'optimization_configuration': self.config,
              'input_fingerprint': self.config['source_fingerprint'],
              'policy_fingerprint': self.config['policy_fingerprint'],
              'selected_schedule': [row], 'candidate': candidate, 'summary': summary,
              'validation_results': {'physical': 'VALID', 'inventory': 'VALID',
                                     'settlement': 'SETTLED', 'carryover': 'VALID'},
              'protected_commitments_in': [],
              'completed_calls': [r['call_id'] for r in completed_rows],
              'carried_inventory': state['inventory'], 'carried_transfers': [],
              'cargo_progress': progress, 'contract_accounting_state': {},
              'in_progress_contract_accounting': {},
              'prior_state_fingerprint': _fingerprint(previous_state) if previous_state else 'initial-state',
              'next_state_fingerprint': _fingerprint(state), 'next_state': state}
        cp['checkpoint_fingerprint'] = _fingerprint(cp)
        _write(self.root / f'window_{i:03d}.json', cp)

    def _tamper(self, i, mutate):
        path = self.root / f'window_{i:03d}.json'
        cp = json.loads(path.read_text())
        mutate(cp)
        cp.pop('checkpoint_fingerprint', None)
        cp['checkpoint_fingerprint'] = _fingerprint(cp)
        _write(path, cp)

    def test_valid_two_window_chain(self):
        result = audit_rolling_run(self.root)
        self.assertEqual(result['status'], 'STRUCTURAL_CHECKPOINT_CHAIN_VALID', result)
        self.assertEqual(result['validated_checkpoint_count'], 2)
        self.assertFalse(result['independent_replay_performed'])
        self.assertEqual(result['windows'][0]['window_projected_demurrage_cents'], 1000)

    def test_valid_three_window_chain(self):
        self.add_window(3)
        result = audit_rolling_run(self.root)
        self.assertEqual(result['status'], 'STRUCTURAL_CHECKPOINT_CHAIN_VALID', result)
        self.assertEqual(result['validated_checkpoint_count'], 3)

    def test_corrupt_payload_detected_even_when_other_fields_unchanged(self):
        path = self.root/'window_001.json'
        data = json.loads(path.read_text())
        data['carried_inventory']['T'] = '0'
        _write(path, data)
        result = audit_rolling_run(self.root)
        self.assertEqual(result['status'], 'INVALID_CHECKPOINT_CHAIN')
        self.assertEqual(result['validated_checkpoint_count'], 0)
        self.assertTrue(any('fingerprint mismatch' in x for x in result['issues']))

    def test_cross_window_fingerprint_detected_even_if_checkpoint_rehashed(self):
        self._tamper(2, lambda cp: cp.update(prior_state_fingerprint='incorrect'))
        result = audit_rolling_run(self.root)
        self.assertTrue(any('fingerprint chain mismatch' in x for x in result['issues']))

    def test_false_final_optimality_detected(self):
        def change(cp):
            domain = cp['candidate']['optimizer_result']['final_candidate_domain']
            domain['optimality_proven'] = False
            cp['candidate']['optimizer_result']['final_domain_optimality_proven'] = False
            cp['summary']['final_domain_optimality_proven'] = False
        self._tamper(1, change)
        result = audit_rolling_run(self.root)
        self.assertEqual(result['status'], 'INVALID_CHECKPOINT_CHAIN')
        self.assertTrue(any('stale OPTIMAL' in x for x in result['issues']))

    def test_no_window_sum_is_reported_as_cumulative(self):
        result = audit_rolling_run(self.root)
        self.assertNotIn('cumulative_demurrage_cents', result)
        self.assertEqual([x['window_projected_demurrage_cents'] for x in result['windows']], [1000,1000])

    def test_completed_cargo_regression_detected(self):
        self._tamper(2, lambda cp: (cp['cargo_progress']['SYN-1'].update(completed_bbl='30',remaining_bbl='70'),
                                    cp['next_state']['cargo_progress']['SYN-1'].update(completed_bbl='30',remaining_bbl='70'),
                                    cp.update(next_state_fingerprint=_fingerprint(cp['next_state']))))
        result = audit_rolling_run(self.root)
        self.assertTrue(any('cargo progress regressed' in x for x in result['issues']))

    def test_missing_window_gap_detected(self):
        (self.root/'window_001.json').unlink()
        result = audit_rolling_run(self.root)
        self.assertEqual(result['status'], 'INVALID_CHECKPOINT_CHAIN')
        self.assertTrue(any('noncontiguous' in x for x in result['issues']))

    def test_legacy_run_rejected_by_default(self):
        manifest = json.loads((self.root/'manifest.json').read_text())
        manifest['config'].pop('rolling_optimizer_mode')
        manifest['run_fingerprint'] = _fingerprint(manifest['config'])
        _write(self.root/'manifest.json', manifest)
        result = audit_rolling_run(self.root)
        self.assertEqual(result['status'], 'INVALID_CHECKPOINT_CHAIN')
        self.assertTrue(any('expected INTEGRATED_COST_AWARE_V1' in x for x in result['issues']))


if __name__ == '__main__':
    unittest.main()
