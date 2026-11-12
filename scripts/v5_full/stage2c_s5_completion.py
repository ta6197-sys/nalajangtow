#!/usr/bin/env python3
"""Run or audit real-source S5 full completion under a fixed Rolling window count.

Additive only. Never alters the optimizer or previous checkpoints. To increase
windows, launch a NEW run with an increased fixed --windows value; changing a
persisted manifest and resuming it would violate checkpoint fingerprints.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.daily_planning.integrated_v3.v5_completion_evidence import audit_s5_completion
from src.daily_planning.integrated_v3.v5_run_review import (
    fingerprint, load_checkpoints, load_json, resolve_run,
)
from src.daily_planning.integrated_v3.v5_scale import (
    execute_scale, load_execution_config, preflight_sources, select_scope,
)
from src.daily_planning.physical_scheduling.v5_user_rules import VERSION as RULE_VERSION

TIE = 'CANONICAL_ETB_BERTH_V1'


def write_new(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        f.write('\n')


def safe_root(path: Path, source: Path) -> Path:
    raw = path.expanduser()
    if raw.is_symlink():
        raise ValueError('Output root cannot be a symlink')
    root, source = raw.resolve(), source.resolve()
    if root == source or source in root.parents or root == ROOT.resolve():
        raise ValueError('Output must be outside the source input directory and repository root')
    return root


def context_for_run(config, snapshot, call_ids, windows):
    from src.daily_planning.integrated_input.provenance import canonical, digest
    daylight_sha = hashlib.sha256(Path(config['pilotage_context']['daylight_path']).read_bytes()).hexdigest()
    return {'source_fingerprint': digest(canonical(snapshot).encode()),
            'policy_fingerprint': fingerprint({
                'policy': config['policy'], 'pilotage_context': config['pilotage_context'],
                'daylight_sha256': daylight_sha, 'pricing_policy': config['pricing_policy'],
                'clock_policy': config['clock_policy'], 'settlement_records': config['settlement_records']}),
            'as_of_at': config['as_of_at'], 'scope': sorted(call_ids), 'windows': windows}


def independently_replay(config_path, output_root, run_id, timeout_seconds):
    cmd = [sys.executable, str(ROOT/'scripts/v5_full/verify_resume.py'),
           '--config', str(config_path), '--root', str(output_root), '--run-id', run_id]
    try:
        run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                             check=False, timeout=timeout_seconds)
        try:
            result = json.loads(run.stdout)
        except json.JSONDecodeError:
            result = {'status':'UNPARSEABLE_OUTPUT'}
        passed = run.returncode == 0 and result.get('status') == 'REQUESTED_WINDOWS_VALIDATED' \
            and result.get('independent_replay') == 'PASS'
        return {'status': 'PASS' if passed else 'FAIL', 'exit_code': run.returncode,
                'result': result, 'stderr_tail': run.stderr[-1500:]}
    except subprocess.TimeoutExpired:
        return {'status':'FAIL', 'reason':'INDEPENDENT_REPLAY_TIMEOUT'}
    except OSError as exc:
        return {'status':'FAIL', 'reason':'INDEPENDENT_REPLAY_EXECUTION_ERROR', 'detail':str(exc)}


def audit_saved(config_path, root, run_id, config, snapshot, scope, timeout_seconds):
    directory = resolve_run(root, run_id)
    execution = load_json(directory/'execution_report.json')
    try:
        manifest, windows = load_checkpoints(directory)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        audit = {'status':'BLOCKED_INVALID_CHECKPOINT_CHAIN', 'run_id':run_id,
                 'execution_status':execution.get('status'),
                 'exception':type(exc).__name__, 'reason':str(exc),
                 'simulation_only':True, 'operational_approval':False,
                 'independent_replay':'NOT_RUN',
                 'note':'The saved execution_report.json remains the primary failure evidence.'}
        write_new(directory/'stage2c_completion_audit.json', audit)
        return audit
    expected_windows = manifest['config'].get('windows')
    if type(expected_windows) is not int or not 1 <= expected_windows <= 65:
        raise ValueError('Persisted window count is unsupported')
    context = context_for_run(config, snapshot, scope, expected_windows)
    # A complete chain can be replayed even if not all five calls finished.
    replay = ({'status':'NOT_RUN'} if len(windows) != expected_windows or
              execution.get('status') != 'REQUESTED_WINDOWS_VALIDATED' else
              independently_replay(config_path, root, run_id, timeout_seconds))
    audit = audit_s5_completion(execution, manifest, windows,
            source_calls={c['call_id']:c for c in snapshot['calls']},
            replay_status=replay['status'],
            expected_source_fingerprint=context['source_fingerprint'],
            expected_policy_fingerprint=context['policy_fingerprint'],
            expected_start_at=context['as_of_at'],
            expected_windows=context['windows'],
            expected_tie_break_mode=TIE)
    audit.update(run_id=run_id, replay_process=replay)
    write_new(directory/'stage2c_completion_audit.json', audit)
    return audit


def main(argv=None):
    ap=argparse.ArgumentParser(description='S5 all-call completion and final exact-cent settlement gate')
    ap.add_argument('--mode', choices=('run','audit-existing'),required=True)
    ap.add_argument('--config',type=Path,required=True)
    ap.add_argument('--output-root',type=Path,required=True)
    ap.add_argument('--run-id',help='Only for audit-existing')
    ap.add_argument('--call-ids-file',type=Path,help='Optional exact source S5 IDs; do not change between attempts')
    ap.add_argument('--windows',type=int,default=3,help='Fixed number of 72h planning windows, advancing 24h')
    ap.add_argument('--seconds-per-window',type=float,default=120)
    ap.add_argument('--candidates-per-pair',type=int,default=6)
    ap.add_argument('--total-candidates',type=int,default=240)
    ap.add_argument('--improvement-rounds',type=int,default=2)
    ap.add_argument('--replay-timeout',type=int,default=600)
    a=ap.parse_args(argv)
    try:
        if (a.mode=='run' and a.run_id) or (a.mode=='audit-existing' and not a.run_id):
            raise ValueError('--run-id belongs only to audit-existing')
        if not 1<=a.windows<=65 or not 1<=a.replay_timeout<=3600:
            raise ValueError('Invalid window count or replay timeout')
        config_path=a.config.expanduser().resolve()
        config=load_execution_config(config_path)
        if config['policy'].get('schema_version') != RULE_VERSION or config['policy'].get('operational_approval') is not False:
            raise ValueError('Explicit synthetic user-confirmed V5 policy is required')
        snapshot,preflight=preflight_sources(config)
        if sum(c.get('fixed_etb_is_hard') is True for c in snapshot['calls'])!=15:
            raise ValueError('Expected 15 hard fixed ETBs in original input')
        ids=None
        if a.call_ids_file is not None:
            ids=json.loads(a.call_ids_file.read_text(encoding='utf-8'))
        scope=select_scope(snapshot,5,ids)
        if len(scope)!=5:
            raise ValueError('Exactly five original source calls required')
        output=safe_root(a.output_root,config['data_dir'])
        if a.mode=='run':
            from src.daily_planning.integrated_v3.v5_scale import bounded_parameters
            bounded_parameters(count=5,windows=a.windows,time_limit_seconds=a.seconds_per_window,
                               candidates_per_pair=a.candidates_per_pair,total_candidates=a.total_candidates,
                               improvement_rounds=a.improvement_rounds)
            report=execute_scale(config,output_root=output,call_count=5,
                call_ids=scope,windows=a.windows,time_limit_seconds=a.seconds_per_window,
                candidates_per_pair=a.candidates_per_pair,total_candidates=a.total_candidates,
                improvement_rounds=a.improvement_rounds,tie_break_mode=TIE,
                snapshot=snapshot,preflight=preflight)
            run_id=report['run_id']
        else:
            run_id=a.run_id
        audit=audit_saved(config_path,output,run_id,config,snapshot,scope,a.replay_timeout)
        print(json.dumps(audit,ensure_ascii=False,indent=2,sort_keys=True))
        return 0 if audit['status']=='PASS_S5_ALL_FIVE_COMPLETED_SETTLED_AND_REPLAYED' else 2
    except (OSError,ValueError,KeyError,TypeError,ImportError,OverflowError) as exc:
        print(json.dumps({'status':'BLOCKED','exception':type(exc).__name__,
            'reason':str(exc),'no_operational_approval':True},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
