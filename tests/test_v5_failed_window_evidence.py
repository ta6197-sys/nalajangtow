"""Stdlib-only tests for source-preserving V5 failed-window evidence."""
import json
import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from src.daily_planning.integrated_v3.v5_failure_evidence import (
    build_failure_evidence, failure_markdown, write_failure_evidence_new,
)


class TestFailureEvidence(unittest.TestCase):
    def report(self, candidate, selected=None):
        return build_failure_evidence(
            window_id=1, as_of_at='2026-11-03T00:00:00+09:00',
            selected_call_ids=selected or ['DEM-0001'], candidate=candidate,
            source_fingerprint='source-hash', policy_fingerprint='policy-hash',
            scenario_id='V5_BALANCED_154',
        )

    def test_zero_candidates_preserves_reason(self):
        r = self.report({
            'status': 'WINDOW_NOT_VALIDATED', 'reason': 'NO_FEASIBLE_GENERATED_OPTION',
            'issues': [{'code': 'NO_FEASIBLE_GENERATED_OPTION', 'call_id': 'DEM-0001'}],
            'optimizer_result': {
                'status': 'NO_FEASIBLE_GENERATED_OPTION',
                'initial_candidate_count': 0, 'final_candidate_count': 0,
                'candidates_by_call_and_berth': {'DEM-0001': {'BERTH-01': []}},
                'rejection_reasons': [{
                    'call_id': 'DEM-0001', 'berth_id': 'BERTH-01',
                    'code': 'PHYSICAL_OPTION_REJECTED', 'reason': 'outside work window'
                }],
            },
        })
        self.assertEqual(r['triage_classification'], 'SOME_CALLS_HAVE_NO_VALIDATED_PHYSICAL_OPTIONS')
        self.assertEqual(r['selected_calls_with_zero_candidates'], ['DEM-0001'])
        self.assertEqual(r['rejection_counts_by_code'], {'PHYSICAL_OPTION_REJECTED': 1})
        self.assertFalse(r['validated'])
        self.assertIn('DEM-0001', failure_markdown(r))

    def test_cp_sat_infeasible_not_global_infeasibility(self):
        r = self.report({
            'status': 'WINDOW_NOT_VALIDATED', 'reason': 'INFEASIBLE',
            'issues': [], 'optimizer_result': {
                'status': 'INFEASIBLE', 'solver_status': 'INFEASIBLE',
                'candidate_domain_fingerprint': 'candidate-sha',
                'candidates_by_call_and_berth': {'DEM-0001': {'BERTH-01': ['2026-11-03T04:00:00+09:00']}},
            }
        })
        self.assertEqual(r['triage_classification'], 'FINITE_CANDIDATE_DOMAIN_INFEASIBLE')
        self.assertEqual(r['selected_calls_with_zero_candidates'], [])
        self.assertIn('FINITE_CANDIDATE_DOMAIN_ONLY', r['infeasibility_claim_scope'])

    def test_no_overwrite(self):
        r = self.report({'status': 'WINDOW_NOT_VALIDATED', 'reason': 'TIME_LIMIT_NO_INCUMBENT'})
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'window_001_failure.json'
            write_failure_evidence_new(p, r)
            self.assertEqual(json.loads(p.read_text())['window_id'], 1)
            with self.assertRaises(FileExistsError):
                write_failure_evidence_new(p, r)

    def test_reject_fake_success(self):
        with self.assertRaises(ValueError):
            self.report({'status': 'VALIDATED'})

    def test_missing_optimizer_result_stays_unknown(self):
        r = self.report({'status': 'WINDOW_NOT_VALIDATED', 'reason': 'UNKNOWN'})
        self.assertEqual(r['triage_classification'], 'UNCLASSIFIED_REQUIRES_SOURCE_LOGS')
        self.assertEqual(r['selected_calls_with_zero_candidates'], [])

    def test_fix_policy_pricing_and_budget_classification(self):
        for reason,expected in [('FIX_PRECHECK_FAILED','HARD_FIX_OPTION_REJECTED'),
            ('UNSUPPORTED_OPERATIONAL_POLICY','OPERATIONAL_POLICY_UNRESOLVED'),
            ('SETTLEMENT_UNRESOLVED','SETTLEMENT_OR_CLOCK_UNRESOLVED'),
            ('TIME_LIMIT_BEFORE_ROLLING_SETTLEMENT_VALIDATION','TIME_BUDGET_NO_VALIDATED_SOLUTION')]:
            with self.subTest(reason=reason):
                report=self.report({'status':'WINDOW_NOT_VALIDATED','reason':reason,'issues':[{'code':reason}]})
                self.assertEqual(report['triage_classification'],expected)

    def test_rejection_counts_include_berth_and_original_reasons(self):
        report=self.report({'status':'WINDOW_NOT_VALIDATED','reason':'NO_FEASIBLE_GENERATED_OPTION',
            'optimizer_result':{'rejection_reasons':[{'call_id':'DEM-0001','berth_id':'BERTH-01','code':'PHYSICAL_OPTION_REJECTED','reason':'pilotage window'}]}})
        self.assertEqual(report['rejection_counts_by_call_and_berth'],{'DEM-0001':{'BERTH-01':1}})
        self.assertEqual(report['rejection_records'][0]['reason'],'pilotage window')


class RollingFailureEvidenceTests(unittest.TestCase):
    def setUp(self):
        from tests.test_v5_rolling import rolling_fixture, START
        self.snapshot,self.policy,self.context=rolling_fixture();self.start=START
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'run'

    def run_roll(self, **kwargs):
        from src.daily_planning.integrated_v3.v5_rolling import run_v5_rolling
        options=dict(data_dir='SYNTHETIC',policy=self.policy,pilotage_context=self.context,
                     as_of_at=self.start,output_dir=self.path,windows=1,time_limit_seconds=30,
                     pricing_policy='CLEAR_WEATHER_BASELINE_V1',rolling_optimizer_mode='INTEGRATED_COST_AWARE_V1')
        options.update(kwargs)
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *a:copy.deepcopy(self.snapshot)), \
             patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}):
            return run_v5_rolling(self.snapshot,**options)

    def test_failed_window_written_and_resume_never_overwrites(self):
        failure={'status':'INFEASIBLE','issues':[{'code':'FIX_PRECHECK_FAILED','call_id':'SYN-1'}]}
        with patch('src.daily_planning.integrated_v3.v5_rolling_optimizer.v5_physical.schedule_v5',return_value=failure):
            first=self.run_roll();path=self.path/'window_001_failure.json';before=path.read_bytes()
            second=self.run_roll(resume=True)
        self.assertEqual(first['status'],'STOPPED_UNRESOLVED')
        self.assertEqual(first['validated_window_count'],0)
        self.assertFalse((self.path/'window_001.json').exists())
        self.assertEqual(json.loads(before)['triage_classification'],'HARD_FIX_OPTION_REJECTED')
        self.assertEqual(path.read_bytes(),before)
        self.assertIn('FileExistsError',second['stopping_reason']['failure_evidence_error'])

    def test_valid_window_has_no_failure_artifact(self):
        result=self.run_roll()
        self.assertEqual(result['status'],'REQUESTED_WINDOWS_VALIDATED',result)
        self.assertTrue((self.path/'window_001.json').is_file())
        self.assertFalse((self.path/'window_001_failure.json').exists())


if __name__ == '__main__':
    unittest.main()
