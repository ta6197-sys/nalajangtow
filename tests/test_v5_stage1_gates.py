"""Source-free evidence tests; no claim of real V5 CP-SAT execution."""
import json
import subprocess
import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.daily_planning.integrated_v3.v5_stage1_evidence import (
    VERSION, GATES, required_window_end, inspect_coverage, review_stage2b, audit_gate,
)
from scripts.v5_stage1 import run_integrated_gates as run

START = datetime(2026, 11, 3, tzinfo=timezone(timedelta(hours=9)))


def at(days=0, hours=0):
    return (START + timedelta(days=days, hours=hours)).isoformat()


def fixture_coverage(days=7, policy_days=7, tanks=3):
    snapshot = {'tanks': [{'tank_id': f'TANK-{i:02d}'} for i in range(1, tanks+1)],
                'berths': [{'berth_id':'BERTH-01', 'active':'Y'}],
                'fixed_process_flows': {'inventory_as_of_at': at(),
                    'flow_horizon_end_at': at(days), 'segments': []}}
    for i in range(1, tanks+1):
        for k in range(4*days):
            snapshot['fixed_process_flows']['segments'].append({
                'tank_id': f'TANK-{i:02d}',
                'start_at': at(hours=6*k), 'end_at': at(hours=6*(k+1))})
    config = {'as_of_at': at(), 'policy': {'berths': {'BERTH-01': {
        'pilotage_windows': [{'start_at': at(), 'end_at': at(policy_days)}],
        'work_windows': [{'start_at': at(), 'end_at': at(policy_days)}]}}}}
    return snapshot,config


def fixture_run(scope=('A','B'), windows=3, completed=True):
    ids = list(scope)
    finished = ids if completed else ['A']
    state_at = at(days=windows)
    state = {'at':state_at,
             'completed_rows':[{'call_id':cid,'etu_at':at(days=1), 'work_end_at':at(hours=12)} for cid in finished],
             'contract_accounting_state':{cid:{'demurrage_cents':150, 'settlement':{'status':'SETTLED'}} for cid in finished},
             'cargo_progress':{cid:{'completed_bbl':'10.25','remaining_bbl':'0'} for cid in finished},
             'previous_schedule':[], 'carried_transfers':[], 'in_progress_contract_accounting':{}}
    cp = [{'next_state':state} for _ in range(windows)]
    report = {'status':'REQUESTED_WINDOWS_VALIDATED',
              'requested_windows':windows, 'validated_window_count':windows,
              'source_fingerprint':'source','scope_call_ids':ids,
              'all_scope_calls_completed':completed,
              'completed_call_ids':finished,'unresolved_accounting_call_ids':[],
              'cumulative_demurrage_cents':150*len(ids) if completed else None,
              'simulation_only':True,'operational_approval':False}
    manifest = {'config':{'windows':windows,'source_fingerprint':'source',
                          'scope':sorted(ids),'tie_break_mode':'CANONICAL_ETB_BERTH_V1'}}
    ev = [{'window_id':i,'has_work':True,'first_domain_hash':'h','final_domain_hash':'h2',
           'physical_schedule_hash':'p','cost_cents':100,
           'final_domain_optimality_proven':True,'solver_status':'OPTIMAL'} for i in range(1,windows+1)]
    calls = {cid: {'cargo_volume_bbl':'10.25'} for cid in ids}
    return report, manifest, cp, ev, calls


def stage2b():
    return {'status':'COMPLETE_DIAGNOSTIC',
      'metadata':{'repetitions':20, 'budgets_seconds_per_window':[60,120],
                  'tie_break_mode':'CANONICAL_ETB_BERTH_V1',
                  'source':{'source_fingerprint':'source','as_of_at':at(),'scope_call_ids':['A','B','C','D','E']}},
      'budgets':{'60':{'status':'OBSERVED_REPEATABLE_AT_THIS_BUDGET',
                        'expected':20,'valid_and_replayed':20,'context_consistent':True,'windows':[]},
                 '120':{'status':'OBSERVED_REPEATABLE_AT_THIS_BUDGET',
                        'expected':20,'valid_and_replayed':20,'context_consistent':True,'windows':[]}}}


class CoverageTests(unittest.TestCase):
    def test_gate_constants_exact(self):
        self.assertEqual(GATES['FULL140'], (140,60))
        self.assertEqual(GATES['R14'], (20,14))
    def test_60_windows_requires_62_days(self):
        self.assertEqual(required_window_end(at(),60), START.astimezone(timezone.utc)+timedelta(days=62))
    def test_flow_and_policy_complete(self):
        s,c = fixture_coverage()
        x=inspect_coverage(s,c,3)
        self.assertEqual(x['status'],'COVERAGE_PRECHECK_PASS')
        self.assertTrue(all(t['continuous_through_required_end'] for t in x['tank_coverage']))
    def test_flow_short(self):
        s,c=fixture_coverage(days=4,policy_days=10)
        x=inspect_coverage(s,c,3)
        self.assertEqual(x['status'],'BLOCKED')
        self.assertIn('FIXED_FLOW_HORIZON_SHORT',[b['code'] for b in x['blockers']])
    def test_policy_short(self):
        s,c=fixture_coverage(days=10,policy_days=2)
        x=inspect_coverage(s,c,3)
        self.assertIn('DECLARED_BERTH_WINDOW_HORIZON_SHORT',[b['code'] for b in x['blockers']])
    def test_missing_fixed_interval(self):
        s,c=fixture_coverage()
        del s['fixed_process_flows']['segments'][3]
        x=inspect_coverage(s,c,3)
        self.assertIn('FIXED_FLOW_GAP_OR_INVALID_INTERVAL',[b['code'] for b in x['blockers']])
    def test_overlapping_fixed_interval(self):
        s,c=fixture_coverage()
        s['fixed_process_flows']['segments'][1]['start_at']=at(hours=5)
        x=inspect_coverage(s,c,3)
        self.assertIn('FIXED_FLOW_OVERLAP',[b['code'] for b in x['blockers']])
    def test_missing_berth_policy(self):
        s,c=fixture_coverage()
        c['policy']['berths']={}
        self.assertIn('MISSING_ACTIVE_BERTH_POLICY',
                      [b['code'] for b in inspect_coverage(s,c,3)['blockers']])
    def test_tz_aware_required(self):
        s,c=fixture_coverage()
        c['as_of_at']='2026-11-03T00:00:00'
        with self.assertRaises(ValueError):inspect_coverage(s,c,3)


class Stage2bTests(unittest.TestCase):
    def check(self,x):
        return review_stage2b(x,source_fingerprint='source',as_of_at=at(),s5_scope=list('ABCDE'))
    def test_40_consistent(self):
        self.assertEqual(self.check(stage2b())['status'],'OBSERVED_REPEATABLE_BOTH_BUDGETS')
    def test_missing_one_iteration(self):
        x=stage2b(); x['budgets']['120']['valid_and_replayed']=19
        self.assertEqual(self.check(x)['status'],'BLOCKED_INVALID_EVIDENCE')
    def test_variation_requires_review(self):
        x=stage2b(); x['budgets']['60']['status']='VARIATION_DETECTED'
        self.assertEqual(self.check(x)['status'],'REVIEW_VARIATION_REQUIRED')
    def test_scope_mismatch(self):
        x=stage2b(); x['metadata']['source']['scope_call_ids']=['other']
        self.assertIn('STAGE2B_S5_SCOPE_MISMATCH',self.check(x)['errors'])
    def test_source_mismatch(self):
        x=stage2b(); x['metadata']['source']['source_fingerprint']='other'
        self.assertIn('STAGE2B_SOURCE_MISMATCH',self.check(x)['errors'])
    def test_invalid_asof(self):
        x=stage2b(); x['metadata']['source']['as_of_at']='bad date'
        self.assertIn('STAGE2B_INVALID_START_TIME',self.check(x)['errors'])
    def test_missing_proof_is_reported(self):
        x=stage2b(); x['budgets']['60']['windows']=[{'window_id':1,'warnings':['CANONICAL_SELECTION_NOT_PROVEN_IN_ALL_RUNS']}]
        self.assertTrue(self.check(x)['proof_warnings'])


class GateAuditTests(unittest.TestCase):
    def audit(self,fixture,gate='FULL140',replay=True,source=None):
        report,manifest,cps,evidence,calls=fixture
        return audit_gate(report,manifest,cps,evidence,gate=gate,scope=list(report['scope_call_ids']),
                          expected_windows=len(cps),source_fingerprint='source',replay_pass=replay,
                          source_calls=calls if source is None else source)
    def test_complete_strict(self):
        a=self.audit(fixture_run())
        self.assertEqual(a['status'],'PASS_SCOPE_COMPLETE')
        self.assertEqual(a['exact_settled_sum_cents_if_complete'],300)
    def test_diagnostic_incomplete_allowed(self):
        a=self.audit(fixture_run(completed=False),gate='S20')
        self.assertEqual(a['status'],'PASS_VALIDATED_WINDOWS_ONLY')
        self.assertEqual(a['remaining_call_ids'],['B'])
    def test_scale_report_uses_checked_checkpoint_ids_and_count(self):
        f=list(fixture_run(completed=False))
        del f[0]['completed_call_ids']
        f[0]['completed_scope_count']=1
        f[2][-1]['completed_calls']=['A']
        self.assertEqual(self.audit(f,gate='S10')['status'],'PASS_VALIDATED_WINDOWS_ONLY')
        f[0]['completed_scope_count']=0
        self.assertIn('REPORTED_COMPLETION_SET_MISMATCH',self.audit(f,gate='S10')['issues'])
        f[0]['completed_scope_count']=1
        f[2][-1]['completed_calls']=['B']
        self.assertIn('REPORTED_COMPLETION_SET_MISMATCH',self.audit(f,gate='S10')['issues'])
    def test_full_scope_incomplete_blocked(self):
        self.assertIn('BLOCKED_SCOPE_NOT_FULLY_COMPLETED',self.audit(fixture_run(completed=False))['status'])
    def test_no_replay_rejected(self):
        self.assertEqual(self.audit(fixture_run(),replay=False)['status'],'BLOCKED_INDEPENDENT_REPLAY_NOT_PASSED')
    def test_cents_mismatch(self):
        f=list(fixture_run()); f[0]['cumulative_demurrage_cents']=299
        self.assertIn('CUMULATIVE_COST_MISMATCH',self.audit(f)['issues'])
    def test_cents_missing(self):
        f=list(fixture_run());f[0]['cumulative_demurrage_cents']=None
        self.assertIn('CUMULATIVE_COST_UNRESOLVED',self.audit(f)['issues'])
    def test_full_cargo_short(self):
        f=list(fixture_run());f[2][-1]['next_state']['cargo_progress']['A']['completed_bbl']='10.24'
        self.assertIn('COMPLETED_CARGO_REMAINDER_NOT_ZERO',self.audit(f)['issues'])
    def test_etu_after_state(self):
        f=list(fixture_run());f[2][-1]['next_state']['completed_rows'][0]['etu_at']=at(days=100)
        self.assertIn('INVALID_COMPLETED_ETU_OR_WORK_END',self.audit(f)['issues'])
    def test_domain_evidence_missing_not_proven(self):
        f=list(fixture_run());f[3][0]['first_domain_hash']=None
        a=self.audit(f)
        self.assertEqual(a['status'],'PASS_SCOPE_COMPLETE')
        self.assertIn('MISSING_DOMAIN_EVIDENCE',[w['code'] for w in a['warnings']])
    def test_physical_evidence_missing_warning(self):
        f=list(fixture_run());f[3][0]['physical_schedule_hash']=None
        self.assertIn('MISSING_PHYSICAL_EVIDENCE',[w['code'] for w in self.audit(f)['warnings']])
    def test_wrong_report_scope(self):
        f=list(fixture_run());f[0]['scope_call_ids']=['A']
        a=audit_gate(f[0],f[1],f[2],f[3],gate='FULL140',scope=['A','B'],expected_windows=3,
                     source_fingerprint='source',replay_pass=True,source_calls=f[4])
        self.assertIn('SCOPE_CHANGED',a['issues'])


class OrchestrationHelpers(unittest.TestCase):
    def test_safe_root_refuses_existing(self):
        with TemporaryDirectory() as p:
            with self.assertRaises(FileExistsError):run.safe_study_root(Path(p),{'data_dir':p+'/source'})
    def test_safe_root_refuses_source_nesting(self):
        with TemporaryDirectory() as p:
            with self.assertRaises(ValueError):run.safe_study_root(Path(p)/'input'/'results',{'data_dir':str(Path(p)/'input')})
    def test_process_writes_preserved_logs(self):
        with TemporaryDirectory() as p:
            result=run.process([sys.executable,'-c','print("hello")'],log_root=Path(p)/'log',timeout=10)
            self.assertEqual(result['exit_code'],0)
            self.assertIn('hello',Path(result['stdout_path']).read_text())
            with self.assertRaises(FileExistsError):run.process([sys.executable,'-c','pass'],log_root=Path(p)/'log',timeout=10)
    def test_run_s5_coverage_block_does_not_execute(self):
        s,c=fixture_coverage(days=2,policy_days=2)
        class Args: s5_start_windows=3; s5_max_windows=4
        with TemporaryDirectory() as p, patch.object(run,'process',side_effect=AssertionError('must not launch')):
            result=run.run_s5(Path(p),Path('/tmp/none.json'),c,s,Args(),{'S5':list('ABCDE')})
        self.assertEqual(result['status'],'BLOCKED_S5_COVERAGE')
    def test_readonly_report_md(self):
        a=run.markdown_summary({'status':'STOPPED_AT_S5','steps':[{'gate':'S5','status':'BLOCKED', 'attempts':[]} ]})
        self.assertIn('STOPPED_AT_S5',a)
        self.assertIn('overlapping',a)


if __name__=='__main__':unittest.main()
