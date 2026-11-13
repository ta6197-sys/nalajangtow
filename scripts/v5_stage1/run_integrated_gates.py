#!/usr/bin/env python3
"""Source-verified, fail-closed Docktimize V5 Stage-1 gate orchestration.

Additive entry point. Never modifies solver, source input, database, previous
checkpoints, or prior evidence. Use a NEW study root for each run. Reuse vetted
Stage-2B raw studies by reanalyzing into a fresh report directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.daily_planning.integrated_v3.v5_stage1_evidence import (  # noqa: E402
    GATES, TIE_BREAK, VERSION, audit_gate, inspect_coverage, review_stage2b,
)

GATE_ORDER = tuple(GATES)
S5_SUCCESS = 'PASS_S5_ALL_FIVE_COMPLETED_SETTLED_AND_REPLAYED'
RULE_VERSION = 'V5_USER_ETU_SWITCHING_20261112_V1'


def scenario_order(config):
    return tuple(g for g in GATE_ORDER if g != ('FULL140' if config.get('scenario') in ('V5_BALANCED_154','V5_BALANCED_154_EXT_20270109') else 'FULL154'))


def read(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > 80_000_000:
        raise ValueError('Missing, symlinked, or oversized evidence: ' + str(p))
    return json.loads(p.read_text(encoding='utf-8'))


def write_new(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())


def git_metadata():
    def run(*args):
        try:
            result = subprocess.run(['git', *args], cwd=ROOT, text=True,
                                    capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None
    return {'sha': run('rev-parse', 'HEAD'), 'dirty': bool(run('status', '--porcelain'))}


def process(argv, *, log_root, timeout=None):
    """Store full logs, never a truncated success claim; bounded child process."""
    log_root = Path(log_root)
    log_root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    cmd = [str(x) for x in argv]
    with (log_root / 'stdout.log').open('x', encoding='utf-8') as out, \
            (log_root / 'stderr.log').open('x', encoding='utf-8') as err:
        try:
            run = subprocess.run(cmd, cwd=ROOT, stdout=out, stderr=err,
                                 check=False, timeout=timeout)
            code = run.returncode
            failure = None
        except subprocess.TimeoutExpired:
            code, failure = None, 'PROCESS_TIMEOUT'
        except OSError as exc:
            code, failure = None, type(exc).__name__ + ':' + str(exc)
    result = {'command': cmd, 'exit_code': code, 'failure': failure,
              'elapsed_seconds': round(time.monotonic() - started, 3),
              'stdout_path': str(log_root / 'stdout.log'),
              'stderr_path': str(log_root / 'stderr.log')}
    write_new(log_root / 'process.json', result)
    return result


def find_single_run(output):
    root = Path(output)
    folders = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink()] if root.is_dir() else []
    if len(folders) != 1:
        raise ValueError('Expected one independent V5 run, got ' + str(len(folders)))
    return folders[0]


def validated_context(config_path, scope_directory=None):
    from src.daily_planning.integrated_v3.v5_scale import (
        load_execution_config, preflight_sources, select_scope,
    )
    from src.daily_planning.integrated_v3.v5_repeatability import digest
    from src.daily_planning.integrated_input.provenance import canonical
    from src.daily_planning.integrated_input.provenance import digest as raw_hash
    config = load_execution_config(config_path)
    if config['policy'].get('schema_version') != RULE_VERSION:
        raise ValueError('Only the verified V5 20261112 user policy is allowed')
    if config['policy'].get('operational_approval') is not False:
        raise ValueError('Operational approval must remain false')
    snapshot, preflight = preflight_sources(config)
    if not preflight.get('data_preflight_pass'):
        raise ValueError('Source V5 preflight did not pass')
    n_fix = sum(c.get('fixed_etb_is_hard') is True for c in snapshot['calls'])
    expected=154 if config.get('scenario') in ('V5_BALANCED_154','V5_BALANCED_154_EXT_20270109') else 140
    if (len(snapshot['calls']), len(snapshot['berths']), len(snapshot['tanks']), n_fix) != (expected, 11, 3, 15):
        raise ValueError(f'Expected {expected} calls, 11 berths, 3 tanks, and 15 FIX ETBs')
    scopes = {}
    for gate in scenario_order(config):
        count,_=GATES[gate]
        supplied = None
        if scope_directory is not None:
            path = Path(scope_directory)/f'{gate}.json'
            if path.is_file():
                supplied = read(path)
        scope = select_scope(snapshot, count, supplied)
        if len(scope) != count:
            raise ValueError('Incorrect source call count in scope '+gate)
        scopes[gate] = scope
    if not set(scopes['S5']) <= set(scopes['S10']) or not set(scopes['S10']) <= set(scopes['S20']):
        raise ValueError('S5, S10 and S20 diagnostic scopes must be nested')
    if set(scopes['R7']) != set(scopes['S20']) or set(scopes['R14']) != set(scopes['S20']):
        raise ValueError('R7/R14 must reuse exactly the S20 source call IDs')
    source_fp = raw_hash(canonical(snapshot).encode('utf-8'))
    if source_fp != digest(snapshot):
        raise ValueError('Canonical source digest implementations disagree')
    return config, snapshot, preflight, scopes, source_fp


def safe_study_root(study_root, config):
    p = Path(study_root).expanduser()
    if p.is_symlink() or p.exists():
        raise FileExistsError('Use an entirely new study root: ' + str(p))
    root = p.resolve()
    source = Path(config['data_dir']).resolve()
    if root == source or source in root.parents or root == ROOT.resolve() or root == Path.home():
        raise ValueError('Unsafe output location')
    return root


def plan(config, snapshot, scopes, through, s5_start, s5_max):
    order=scenario_order(config)
    if through not in order:raise ValueError('Gate does not belong to selected scenario')
    selected = order[:order.index(through) + 1]
    rows = []
    for gate in selected:
        c, windows = GATES[gate]
        if gate == 'S5':
            windows = s5_start
        coverage = inspect_coverage(snapshot, config, windows)
        rows.append({'gate': gate, 'call_count': c, 'windows': windows,
                     'scope_call_ids': scopes[gate], 'coverage': coverage,
                     'status': coverage['status']})
    return {'version': VERSION, 'mode': 'PREFLIGHT_ONLY_NO_SOLVER',
            'as_of_at': config['as_of_at'], 'through': through,
            's5_first_windows': s5_start, 's5_max_windows': s5_max,
            'gates': rows, 'simulation_only': True, 'operational_approval': False}


def run_stage2b(root, args, config_path, source_fp, scopes, as_of_at, config):
    raw = root / 'stage2b_raw'
    if args.stage2b_existing_study:
        raw = args.stage2b_existing_study.expanduser().resolve()
        if not (raw / 'study_meta.json').is_file():
            return {'status': 'BLOCKED_MISSING_EXISTING_STAGE2B_STUDY'}
        run_record = {'status': 'REUSING_UNMODIFIED_RAW_STAGE2B_EVIDENCE', 'study_root': str(raw)}
    else:
        command = [sys.executable, str(ROOT/'scripts/v5_stability/stage2b_budget_audit.py'),
                   '--mode', 'run', '--config', str(config_path),
                   '--study-root', str(raw), '--budget', '60', '--budget', '120',
                   '--repetitions', '20', '--tie-break-mode', TIE_BREAK,
                   '--candidates-per-pair', str(args.candidates_per_pair),
                   '--total-candidates', str(args.total_candidates),
                   '--improvement-rounds', str(args.improvement_rounds),
                   '--call-ids-file', str(root/'scopes/S5.json')]
        run_record = process(command, log_root=root/'logs/stage2b_run', timeout=None)
    analysis_root = root/'stage2b_reanalysis'
    command = [sys.executable, str(ROOT/'scripts/v5_stability/stage2b_budget_audit.py'),
               '--mode', 'analyze', '--study-root', str(raw), '--report-root', str(analysis_root)]
    analysis_process = process(command, log_root=root/'logs/stage2b_analyze', timeout=600)
    path = analysis_root/'stage2b_report.json'
    if not path.is_file():
        return {'status': 'BLOCKED_STAGE2B_REPORT_NOT_GENERATED',
                'raw_execution': run_record, 'analysis_process': analysis_process}
    report = read(path)
    verdict = review_stage2b(report, source_fingerprint=source_fp,
                             as_of_at=as_of_at, s5_scope=scopes['S5'], config=config)
    verdict.update(report_path=str(path), raw_execution=run_record,
                   analysis_process=analysis_process,
                   report_recommendation=report.get('recommendation'))
    return verdict


def audit_saved_larger_gate(folder, *, config_path, output_root, gate, scope,
                            windows, source_fp, replay_timeout, source_calls=None):
    from src.daily_planning.integrated_v3.v5_run_review import load_checkpoints
    from src.daily_planning.integrated_v3.v5_repeatability import read_window
    execution = read(folder/'execution_report.json')
    if execution.get('status') != 'REQUESTED_WINDOWS_VALIDATED':
        return {'status': 'BLOCKED_EXECUTION_NOT_FULLY_VALIDATED',
                'execution_status': execution.get('status'),
                'execution_error': execution.get('error_message'),
                'completed_count': execution.get('completed_scope_count'),
                'requested_windows': windows}
    manifest, checkpoints = load_checkpoints(folder)
    if len(checkpoints) != windows:
        return {'status': 'BLOCKED_PARTIAL_CHECKPOINT_CHAIN',
                'observed_windows': len(checkpoints), 'expected_windows': windows}
    replay = process([sys.executable, str(ROOT/'scripts/v5_full/verify_resume.py'),
                      '--config', str(config_path), '--root', str(output_root),
                      '--run-id', folder.name], log_root=folder.parent.parent/'replay_log',
                     timeout=replay_timeout)
    try:
        replay_result = read(Path(replay['stdout_path']))  # raw logs are JSON from verify_resume.py
    except (ValueError, json.JSONDecodeError, TypeError, OSError):
        replay_result = {'status': 'INVALID_REPLAY_OUTPUT'}
    replay_pass = (replay['exit_code'] == 0 and replay_result.get('status') == 'REQUESTED_WINDOWS_VALIDATED'
                   and replay_result.get('independent_replay') == 'PASS'
                   and replay_result.get('validated_window_count') == windows)
    evidence = [read_window(cp) for cp in checkpoints]
    result = audit_gate(execution, manifest, checkpoints, evidence,
                        gate=gate, scope=scope, expected_windows=windows,
                        source_fingerprint=source_fp, replay_pass=replay_pass,
                        source_calls=source_calls)
    result['replay_process'] = replay
    result['run_id'] = folder.name
    return result


def run_s5(root, config_path, config, snapshot, args, scopes):
    """Increase S5 horizon only if the previous complete chain was valid but incomplete."""
    records = []
    for windows in range(args.s5_start_windows, args.s5_max_windows + 1):
        coverage = inspect_coverage(snapshot, config, windows)
        if coverage['status'] != 'COVERAGE_PRECHECK_PASS':
            return {'status': 'BLOCKED_S5_COVERAGE', 'window_count': windows,
                    'coverage': coverage, 'attempts': records}
        root_gate = root/'steps'/'S5'/f'windows_{windows:02d}'
        cmd = [sys.executable, str(ROOT/'scripts/v5_full/stage2c_s5_completion.py'),
               '--mode', 'run', '--config', str(config_path),
               '--output-root', str(root_gate/'runs'), '--windows', str(windows),
               '--call-ids-file', str(root/'scopes/S5.json'),
               '--seconds-per-window', str(args.seconds_per_window),
               '--candidates-per-pair', str(args.candidates_per_pair),
               '--total-candidates', str(args.total_candidates),
               '--improvement-rounds', str(args.improvement_rounds),
               '--replay-timeout', str(args.replay_timeout)]
        proc = process(cmd, log_root=root_gate/'logs',
                       timeout=args.execution_timeout or (windows*args.seconds_per_window*4 + args.replay_timeout + 300))
        try:
            folder = find_single_run(root_gate/'runs')
            audit = read(folder/'stage2c_completion_audit.json')
            record = {'windows': windows, 'run_id': folder.name,
                      'run_directory': str(folder), 'execution': proc,
                      'audit_status': audit.get('status'), 'audit': audit}
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            record = {'windows': windows, 'execution': proc,
                      'audit_status': 'BLOCKED_UNREADABLE_S5_EVIDENCE',
                      'exception': type(exc).__name__, 'reason': str(exc)}
        write_new(root_gate/'step_record.json', record)
        records.append(record)
        if record['audit_status'] == S5_SUCCESS and proc['exit_code'] == 0:
            return {'status': 'PASS_S5_FULL_COMPLETION', 'attempts': records}
        if record['audit_status'] != 'VALIDATED_WINDOWS_SCOPE_INCOMPLETE':
            return {'status': 'BLOCKED_S5_' + record['audit_status'], 'attempts': records}
    return {'status': 'BLOCKED_S5_NOT_COMPLETED_WITHIN_REQUESTED_WINDOWS',
            'attempts': records, 'max_windows': args.s5_max_windows}


def run_scaled_gate(root, gate, config_path, config, snapshot, args, scopes, source_fp):
    count, windows = GATES[gate]
    coverage = inspect_coverage(snapshot, config, windows)
    if coverage['status'] != 'COVERAGE_PRECHECK_PASS':
        return {'status': 'BLOCKED_' + gate + '_COVERAGE', 'coverage': coverage}
    root_gate = root/'steps'/gate
    outroot = root_gate/'runs'
    cmd = [sys.executable, str(ROOT/'scripts/v5_full/run_stage.py'),
           '--config', str(config_path), '--gate', gate,
           '--output-root', str(outroot), '--seconds-per-window', str(args.seconds_per_window),
           '--tie-break-mode', TIE_BREAK,
           '--candidates-per-pair', str(args.candidates_per_pair),
           '--total-candidates', str(args.total_candidates),
           '--improvement-rounds', str(args.improvement_rounds),
           '--call-ids-file', str(root/'scopes'/f'{gate}.json')]
    proc = process(cmd, log_root=root_gate/'execution_log',
                   timeout=args.execution_timeout or (windows*args.seconds_per_window*4 + 600))
    try:
        folder = find_single_run(outroot)
        verdict = audit_saved_larger_gate(folder, config_path=config_path,
                                          output_root=outroot, gate=gate,
                                          scope=scopes[gate], windows=windows,
                                          source_fp=source_fp, replay_timeout=args.replay_timeout,
                                          source_calls={c['call_id']: c for c in snapshot['calls']})
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        verdict = {'status': 'BLOCKED_INVALID_PERSISTED_RUN',
                   'error_type': type(exc).__name__, 'reason': str(exc)}
    verdict['gate'] = gate
    verdict['execution_process'] = proc
    write_new(root_gate/'step_record.json', verdict)
    return verdict


def markdown_summary(data):
    lines = ['# Docktimize V5 — Stage 1 Integrated Gate Report', '',
             '**Overall status:** '+data['status'], '',
             '**Source:** synthetic V5; no operational approval', '',
             '| Gate | Status | Completed | Scope | Windows | Independent Replay |',
             '|---|---|---:|---:|---:|---|']
    for entry in data.get('steps', []):
        audit = entry.get('audit') or entry
        if entry.get('gate') == 'S5':
            attempts = entry.get('attempts') or []
            last = attempts[-1].get('audit') if attempts else {}
            audit = last or {}
            count = audit.get('completed_count', '—')
            window_count = audit.get('requested_windows', '—')
            replay = audit.get('independent_replay', 'NOT_RUN')
            scope_count = 5
        else:
            count = audit.get('completed_count', '—')
            scope_count = audit.get('scope_count', '—')
            window_count = audit.get('requested_windows', '—')
            replay = audit.get('independent_replay', 'NOT_RUN')
        lines.append('| {} | {} | {} | {} | {} | {} |'.format(
            entry.get('gate'), entry.get('status'), count, scope_count, window_count, replay))
    lines += ['', 'Do not add overlapping 72-hour projected costs. Cumulative amounts are',
              'valid only when verified against unique settled records.', '',
              'OPTIMAL in a finite candidate set does not prove global 140-call optimality.',
              'Missing/pending policy or flow evidence is a blocker, never a zero/default.', '']
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description='V5 Stage-1 gates: 40-run evidence, S5 completion, S10/S20/R7/R14/FULL140')
    ap.add_argument('--mode', choices=('preflight', 'run'), required=True)
    ap.add_argument('--config', type=Path, required=True)
    ap.add_argument('--study-root', type=Path)
    ap.add_argument('--through', choices=GATE_ORDER, default='FULL140')
    ap.add_argument('--scope-directory', type=Path,
                    help='Optional JSON call ID lists per gate; all scopes must be nested')
    ap.add_argument('--stage2b-existing-study', type=Path,
                    help='Reuse raw Stage 2B study. A NEW report is always regenerated.')
    ap.add_argument('--s5-start-windows', type=int, default=3)
    ap.add_argument('--s5-max-windows', type=int, default=8)
    ap.add_argument('--seconds-per-window', type=float, default=120)
    ap.add_argument('--candidates-per-pair', type=int, default=6)
    ap.add_argument('--total-candidates', type=int, default=240)
    ap.add_argument('--improvement-rounds', type=int, default=2)
    ap.add_argument('--replay-timeout', type=int, default=900)
    ap.add_argument('--execution-timeout', type=float, default=0)
    args = ap.parse_args(argv)
    try:
        from src.daily_planning.integrated_v3.v5_scale import bounded_parameters
        if not 3 <= args.s5_start_windows <= args.s5_max_windows <= 65:
            raise ValueError('S5 windows must satisfy 3 <= start <= maximum <= 65')
        if not 1 <= args.replay_timeout <= 7200 or args.execution_timeout < 0:
            raise ValueError('Invalid bounded process timeouts')
        if args.mode == 'run' and args.study_root is None:
            raise ValueError('Run mode requires a new --study-root')
        config_path = args.config.expanduser().resolve()
        config, snapshot, preflight, scopes, source_fp = validated_context(config_path, args.scope_directory)
        order=scenario_order(config)
        if args.through not in order:raise ValueError('Gate does not belong to selected scenario')
        for gate in order[:order.index(args.through) + 1]:
            count, windows = GATES[gate]
            bounded_parameters(count=count, windows=windows,
                               time_limit_seconds=args.seconds_per_window,
                               candidates_per_pair=args.candidates_per_pair,
                               total_candidates=args.total_candidates,
                               improvement_rounds=args.improvement_rounds,scenario=config.get('scenario','V5_3TANK'))
        report = plan(config, snapshot, scopes, args.through,
                      args.s5_start_windows, args.s5_max_windows)
        report['source_fingerprint'] = source_fp
        if args.mode == 'preflight':
            print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
            return 0 if all(x['status']=='COVERAGE_PRECHECK_PASS' for x in report['gates']) else 2
        root = safe_study_root(args.study_root, config)
        root.mkdir(parents=True)
        for gate in order:
            write_new(root/'scopes'/f'{gate}.json', scopes[gate])
        stage_manifest = {
            'version': VERSION, 'git': git_metadata(), 'config_path': str(config_path),
            'source_fingerprint': source_fp, 'as_of_at': config['as_of_at'],
            'policy_version': RULE_VERSION, 'tie_break_mode': TIE_BREAK,
            'through': args.through, 'seconds_per_window': args.seconds_per_window,
            'candidate_settings': {'per_pair': args.candidates_per_pair,
                                   'total': args.total_candidates, 'improvement_rounds': args.improvement_rounds},
            'created_at': datetime.now(timezone.utc).isoformat(),
            'simulation_only': True, 'operational_approval': False}
        if config.get('scenario') in ('V5_BALANCED_154','V5_BALANCED_154_EXT_20270109'):stage_manifest['scenario_id']='V5_BALANCED_154'
        if config.get('scenario')=='V5_BALANCED_154_EXT_20270109':stage_manifest['source_profile_id']=config['scenario']
        write_new(root/'stage1_manifest.json', stage_manifest)
        write_new(root/'preflight_report.json', report)
        history = []
        stage2b = run_stage2b(root, args, config_path, source_fp, scopes, config['as_of_at'], config)
        write_new(root/'stage2b_gate.json', stage2b)
        history.append({'gate': 'STAGE2B', 'status': stage2b['status'], 'audit': stage2b})
        blocked = None
        if stage2b['status'] != 'OBSERVED_REPEATABLE_BOTH_BUDGETS':
            blocked = 'STAGE2B'
        if not blocked:
            s5 = run_s5(root, config_path, config, snapshot, args, scopes)
            history.append({'gate': 'S5', **s5})
            if s5['status'] != 'PASS_S5_FULL_COMPLETION':
                blocked = 'S5'
        if not blocked:
            selected = order[1:order.index(args.through) + 1]
            for gate in selected:
                result = run_scaled_gate(root, gate, config_path, config,
                                         snapshot, args, scopes, source_fp)
                history.append({'gate': gate, **result})
                if not result['status'].startswith('PASS_'):
                    blocked = gate
                    break
        final = {'version': VERSION, 'status': 'STOPPED_AT_' + blocked if blocked else
                 'PASS_ALL_REQUESTED_GATES_SOURCE_VERIFIED_SYNTHETIC',
                 'source_fingerprint': source_fp, 'through': args.through,
                 'stopped_at': blocked, 'study_root': str(root),
                 'steps': history, 'simulation_only': True, 'operational_approval': False,
                 'full_period_global_optimality_proven': False}
        write_new(root/'stage1_summary.json', final)
        with (root/'stage1_summary.md').open('x', encoding='utf-8') as out:
            out.write(markdown_summary(final))
        print(json.dumps({'status': final['status'], 'stopped_at': blocked,
                          'study_root': str(root),
                          'summary': str(root/'stage1_summary.json')}, ensure_ascii=False, indent=2))
        return 0 if not blocked else 2
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        print(json.dumps({'status': 'BLOCKED_INVALID_OR_INCOMPLETE_INPUT',
                          'error_type': type(exc).__name__, 'reason': str(exc),
                          'simulation_only': True}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
