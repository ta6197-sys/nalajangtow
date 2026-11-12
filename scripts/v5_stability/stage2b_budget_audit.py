#!/usr/bin/env python3
"""V5 Stage 2B: bounded, reproducible end-to-end S5 budget study.

New local outputs only. Invokes existing run_stage.py and verify_resume.py;
never changes optimizer, physical rules, database, or source input files.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.daily_planning.integrated_v3.v5_budget_evidence import (  # noqa:E402
    VERSION, budget_report, compare_budgets,
)
from src.daily_planning.integrated_v3.v5_repeatability import (  # noqa:E402
    canonical, digest, read_run,
)
from src.daily_planning.integrated_v3.v5_run_review import load_checkpoints  # noqa:E402

RULE_VERSION = 'V5_USER_ETU_SWITCHING_20261112_V1'
MODES = ('NONE','CANONICAL_ETB_BERTH_V1')


def read(path: Path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing or symlinked input: '+str(path))
    return json.loads(path.read_text(encoding='utf-8'))


def write_new(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)
        f.write('\n')


def git_metadata():
    def cmd(*a):
        cp=subprocess.run(['git',*a],cwd=REPO,capture_output=True,text=True,check=False)
        return cp.stdout.strip() if cp.returncode==0 else None
    return {'sha':cmd('rev-parse','HEAD'),'dirty':bool(cmd('status','--porcelain'))}


def source_context(path: Path, ids_file: Path | None):
    from src.daily_planning.integrated_v3.v5_scale import load_execution_config, preflight_sources, select_scope
    cfg=load_execution_config(path)
    if cfg['policy'].get('schema_version') != RULE_VERSION:
        raise ValueError('Only user-confirmed V5 20261112 policy is permitted')
    if cfg['policy'].get('operational_approval') is not False:
        raise ValueError('Never represent this synthetic study as operational approval')
    snap,preflight=preflight_sources(cfg)
    if not preflight.get('data_preflight_pass'):
        raise ValueError('Input preflight did not pass')
    fix=sum(c.get('fixed_etb_is_hard') is True for c in snap['calls'])
    expected=154 if cfg.get('scenario')=='V5_BALANCED_154' else 140
    if (len(snap['calls']),len(snap['berths']),len(snap['tanks']),fix)!=(expected,11,3,15):
        raise ValueError('Unexpected V5 source dimensions or FIX counts')
    ids=read(ids_file) if ids_file else None
    scope=select_scope(snap,5,ids)
    if len(scope)!=5:
        raise ValueError('S5 must contain exactly 5 calls')
    day=Path(cfg['pilotage_context']['daylight_path']).read_bytes()
    info={'config_path':str(path.resolve()),'source_fingerprint':digest(snap),
          'policy_hash':digest(cfg['policy']),'pilotage_hash':digest(cfg['pilotage_context']),
          'daylight_sha256':hashlib.sha256(day).hexdigest(),
          'settlement_hash':digest({'pricing_policy':cfg['pricing_policy'],'clock_policy':cfg['clock_policy'],
                                    'settlement_records':cfg['settlement_records']}),
          'as_of_at':cfg['as_of_at'],'scope_call_ids':scope,
          'data_dir':str(cfg['data_dir'])}
    return info


def command(argv: list[str], *, timeout: float) -> dict:
    start=time.monotonic()
    try:
        proc=subprocess.run(argv,cwd=REPO,capture_output=True,text=True,check=False,timeout=timeout)
        return {'exit_code':proc.returncode,'elapsed_seconds':time.monotonic()-start,
                'stdout_tail':proc.stdout[-6000:],'stderr_tail':proc.stderr[-2500:]}
    except subprocess.TimeoutExpired:
        return {'exit_code':None,'elapsed_seconds':time.monotonic()-start,'error':'PROCESS_TIMEOUT'}
    except OSError as e:
        return {'exit_code':None,'elapsed_seconds':time.monotonic()-start,'error':type(e).__name__+': '+str(e)}


def ensure_safe_new_root(root: Path, data: Path):
    root=root.resolve();data=data.resolve()
    if root.exists():
        raise FileExistsError('Study root exists; never overwrite study evidence: '+str(root))
    if root==data or data in root.parents:
        raise ValueError('Output cannot be inside the source-data folder')
    if root==REPO or root==Path.home():
        raise ValueError('Unsafe output root')
    root.mkdir(parents=True)


def trial_run(root: Path, meta: dict, config: Path, *, budget: int, iteration: int,
              execution_timeout: int, replay_timeout: int) -> dict:
    trial_root=root/'trials'/f'budget_{budget}'/f'repeat_{iteration:03d}'
    run_root=trial_root/'runs'
    if trial_root.exists():
        raise FileExistsError('Repeat directory already exists')
    trial_root.mkdir(parents=True)
    argv=[sys.executable,str(REPO/'scripts/v5_full/run_stage.py'),
          '--config',str(config),'--gate','S5','--output-root',str(run_root),
          '--seconds-per-window',str(budget),
          '--candidates-per-pair',str(meta['candidates_per_pair']),
          '--total-candidates',str(meta['total_candidates']),
          '--improvement-rounds',str(meta['improvement_rounds']),
          '--tie-break-mode',meta['tie_break_mode'],
          '--call-ids-file',str(root/'scope_call_ids.json')]
    started=time.monotonic()
    execution=command(argv,timeout=execution_timeout)
    errors=[]
    directories=sorted(p for p in run_root.iterdir() if p.is_dir()) if run_root.is_dir() else []
    if len(directories)!=1:
        errors.append('EXPECTED_ONE_ISOLATED_RUN_DIRECTORY_GOT_'+str(len(directories)))
    directory=directories[0] if len(directories)==1 else None
    report={}
    manifest={}
    checkpoint_count=0
    structural_valid=False
    if directory is not None:
        try:
            report=read(directory/'execution_report.json')
            manifest, checkpoints=load_checkpoints(directory)
            checkpoint_count=len(checkpoints)
            structural_valid=(checkpoint_count==2 and
                all((cp.get('validation_results') or {}).get('carryover')=='VALID' for cp in checkpoints))
            mc=manifest.get('config') or {}
            if (mc.get('source_fingerprint')!=meta['source']['source_fingerprint'] or
                mc.get('scope')!=meta['source']['scope_call_ids'] or
                mc.get('time_limit_seconds')!=budget or
                mc.get('tie_break_mode','NONE')!=meta['tie_break_mode']):
                errors.append('MANIFEST_CONFIGURATION_MISMATCH')
        except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
            errors.append('STRUCTURAL_OR_REPORT_ERROR:'+type(exc).__name__+':'+str(exc))
    replay=None
    replay_result=None
    if directory is not None and report.get('status')=='REQUESTED_WINDOWS_VALIDATED' and structural_valid and not errors:
        replay=command([sys.executable,str(REPO/'scripts/v5_full/verify_resume.py'),
                        '--config',str(config),'--root',str(run_root),
                        '--run-id',directory.name],timeout=replay_timeout)
        try:
            replay_result=json.loads(replay.get('stdout_tail') or '{}')
        except json.JSONDecodeError:
            errors.append('UNREADABLE_REPLAY_JSON')
        if not (replay['exit_code']==0 and isinstance(replay_result,dict) and
                replay_result.get('status')=='REQUESTED_WINDOWS_VALIDATED' and
                replay_result.get('independent_replay')=='PASS' and
                replay_result.get('validated_window_count')==2):
            errors.append('INDEPENDENT_REPLAY_FAILED')
    else:
        errors.append('EXECUTION_OR_STRUCTURE_NOT_VALIDATED')
    if execution.get('exit_code')!=0:
        errors.append('RUN_STAGE_EXIT_CODE_'+str(execution.get('exit_code')))
    passed=not errors and replay is not None
    record={'iteration':iteration,'budget_seconds_per_window':budget,
            'status':'PASS' if passed else 'FAIL','errors':errors,
            'run_id':directory.name if directory else None,
            'run_root':str(run_root),'execution_status':report.get('status'),
            'structural_valid':structural_valid,'checkpoint_count':checkpoint_count,
            'replay_status':replay_result.get('independent_replay') if isinstance(replay_result,dict) else 'NOT_RUN',
            'all_scope_calls_completed':report.get('all_scope_calls_completed'),
            'cumulative_demurrage_cents':report.get('cumulative_demurrage_cents'),
            'source_fingerprint':report.get('source_fingerprint'),
            'policy_fingerprint':(manifest.get('config') or {}).get('policy_fingerprint'),
            'scope_call_ids':report.get('scope_call_ids'),
            'as_of_at':(manifest.get('config') or {}).get('start_at'),
            'elapsed_total_seconds':time.monotonic()-started,
            'run_stage_process':execution,'replay_process':replay}
    write_new(trial_root/'trial_record.json',record)
    return record


def mode_run(args):
    if not args.config:
        raise ValueError('--config required in run mode')
    config=args.config.resolve()
    if args.repetitions<1 or args.repetitions>100:
        raise ValueError('Repetitions must be 1..100')
    if args.candidates_per_pair<1 or args.candidates_per_pair>24 or args.total_candidates<1 or args.total_candidates>12000 or args.improvement_rounds not in (1,2):
        raise ValueError('Invalid existing V5 search limits')
    if len(args.budget)<2 or len(args.budget)!=len(set(args.budget)) or any(b<1 or b>1800 for b in args.budget):
        raise ValueError('Use at least two distinct budgets of 1..1800 seconds')
    if args.tie_break_mode not in MODES:
        raise ValueError('Invalid tie-break mode')
    ctx=source_context(config,args.call_ids_file)
    root=args.study_root.resolve()
    ensure_safe_new_root(root,Path(ctx['data_dir']))
    write_new(root/'scope_call_ids.json',ctx['scope_call_ids'])
    meta={'version':VERSION,'created_at':datetime.now().astimezone().isoformat(),
          'git':git_metadata(),'python':platform.python_version(),
          'ortools_version':None,'repetitions':args.repetitions,
          'budgets_seconds_per_window':sorted(args.budget),
          'candidates_per_pair':args.candidates_per_pair,
          'total_candidates':args.total_candidates,'improvement_rounds':args.improvement_rounds,
          'tie_break_mode':args.tie_break_mode,
          'source':ctx,'execution_timeout_seconds':args.execution_timeout,
          'replay_timeout_seconds':args.replay_timeout}
    try:
        import ortools
        meta['ortools_version']=ortools.__version__
    except ImportError:
        pass
    write_new(root/'study_meta.json',meta)
    print('S5 Stage2B study:',root,flush=True)
    all_pass=True
    for budget in sorted(args.budget):
        for iteration in range(1,args.repetitions+1):
            record=trial_run(root,meta,config,budget=budget,iteration=iteration,
                             execution_timeout=args.execution_timeout or max(180,4*budget+180),
                             replay_timeout=args.replay_timeout)
            print(f'Budget {budget:4d}s / repeat {iteration:02d}/{args.repetitions} => {record["status"]}',flush=True)
            all_pass=all_pass and record['status']=='PASS'
    return 0 if all_pass else 2


def load_records(root:Path,meta:dict):
    result={}
    for budget in meta['budgets_seconds_per_window']:
        records=[]
        for i in range(1,meta['repetitions']+1):
            path=root/'trials'/f'budget_{budget}'/f'repeat_{i:03d}'/'trial_record.json'
            if path.is_file():
                record=read(path)
            else:
                record={'iteration':i,'status':'MISSING','errors':['MISSING_REPEAT_FILE']}
            if record.get('status')=='PASS':
                try:
                    runroot=Path(record['run_root'])
                    saved=runroot/record['run_id']
                    manifest,windows=load_checkpoints(saved)
                    report=read(saved/'execution_report.json')
                    conf=manifest.get('config') or {}
                    if (conf.get('source_fingerprint')!=meta['source']['source_fingerprint'] or
                        conf.get('scope')!=meta['source']['scope_call_ids'] or
                        conf.get('time_limit_seconds')!=budget or
                        conf.get('tie_break_mode','NONE')!=meta['tie_break_mode'] or
                        len(windows)!=2):
                        raise ValueError('Saved checkpoint/manifest differs from study metadata')
                    evidence=read_run(report,windows,structural_valid=True,
                                      replay_status=record['replay_status'],
                                      policy_fingerprint=conf.get('policy_fingerprint'))
                    for enriched, checkpoint in zip(evidence['windows'], windows):
                        candidate = checkpoint.get('candidate') or {}
                        optimizer = candidate.get('optimizer_result') or {}
                        tiebreak = optimizer.get('schedule_tiebreak') or {}
                        enriched['canonical_status'] = tiebreak.get('status', 'NOT_APPLIED')
                        enriched['runtime_seconds'] = optimizer.get('runtime_seconds')
                        enriched['final_candidate_count'] = optimizer.get('final_candidate_count')
                        enriched['candidate_expansion_rounds'] = optimizer.get('candidate_expansion_rounds')
                    record=dict(record,windows=evidence['windows'])
                    if not evidence['fully_validated_windows'] or record.get('replay_status')!='PASS' or record.get('structural_valid') is not True:
                        raise ValueError('Window chain or replay not fully validated')
                except (OSError,ValueError,KeyError,TypeError) as e:
                    record=dict(record,status='FAIL',errors=['SAVED_EVIDENCE_INVALID:'+str(e)],windows=[])
            records.append(record)
        result[budget]=records
    return result


def recommend_next(summaries):
    if any(x['status'] in ('INCOMPLETE_OR_FAILED', 'INCOMPLETE_EVIDENCE', 'CONTEXT_MISMATCH') for x in summaries.values()):
        return 'INVESTIGATE_FAILED_OR_INCOMPLETE_EVIDENCE_FIRST'
    windows = [w for value in summaries.values() for w in value['windows']]
    flags = {flag for w in windows for flag in w['diagnostic_flags']}
    warnings = {warning for w in windows for warning in w.get('warnings', [])}
    if 'INITIAL_DOMAIN_VARIATION' in flags:
        return 'INSPECT_CANDIDATE_GENERATION_AND_SHARED_TIME_BUDGET'
    if 'FINAL_DOMAIN_VARIATION' in flags:
        return 'INSPECT_COST_AWARE_EXPANSION_AND_STOPPING_BUDGET'
    if 'COST_VARIATION' in flags:
        return 'INSPECT_SOLVER_STATUS_BOUNDS_AND_TIME_BUDGET'
    if flags.intersection({'SCHEDULE_VARIATION', 'PHYSICAL_SCHEDULE_VARIATION'}):
        return 'INSPECT_CANONICAL_SELECTION_PROOF_AND_FALLBACK'
    if warnings:
        return 'INVESTIGATE_UNPROVEN_CANONICAL_OR_FINITE_DOMAIN_STATUS'
    return 'PROCEED_TO_STAGE2C_S5_COMPLETION_GATE'


def mode_analyze(args):
    root=args.study_root.resolve()
    output = args.report_root.resolve() if args.report_root else root
    if args.report_root and (output == root or root in output.parents or output in root.parents):
        raise ValueError('Report root must be separate from the source study')
    meta=read(root/'study_meta.json')
    if meta.get('version')!=VERSION:
        raise ValueError('Unexpected study version')
    runs=load_records(root,meta)
    summaries={b:budget_report(records,expected=meta['repetitions'],budget=b) for b,records in runs.items()}
    cross=compare_budgets(runs)
    recommendation=recommend_next(summaries)
    result={'version':VERSION,'scope':'SOURCE_VERIFIED_S5_2_WINDOWS_BUDGET_EVIDENCE_ONLY',
            'status':'COMPLETE_DIAGNOSTIC' if all(x['status'] not in ('INCOMPLETE_OR_FAILED','INCOMPLETE_EVIDENCE','CONTEXT_MISMATCH') for x in summaries.values()) else 'PARTIAL_DIAGNOSTIC',
            'metadata':meta,'budgets':{str(b):v for b,v in summaries.items()},
            'cross_budget':cross,'recommendation':recommendation,
            'limitations':['No full five-call completion requirement in S5 two-window gate',
                           'Overlapping window costs are not cumulative',
                           'These findings do not establish global 140-call optimality',
                           'No optimizer algorithms or operational rules changed']}
    write_new(output/'stage2b_report.json',result)
    lines=['# Docktimize Stage 2B — S5 Budget Evidence','',
           f'Analysis status: **{result["status"]}**','',
           f'Recommended next action: **{recommendation}**','',
           '| Budget per window | PASS / expected | Status | 5 calls completed | Cumulative cost resolved |',
           '|---:|---:|---|---:|---:|']
    for b,v in sorted(summaries.items()):
        lines.append(f'| {b}s | {v["valid_and_replayed"]}/{v["expected"]} | {v["status"]} | {v["all_scope_completed_runs"]} | {v["cumulative_cost_resolved_runs"]} |')
        for w in v['windows']:
            lines.append(f'\nBudget {b}s, window {w["window_id"]}: {w["diagnostic_flags"]}, ETB max spread {w["max_etb_span_minutes"]} minutes, cost {w["cost_min_cents"]}..{w["cost_max_cents"]} cents; canonical proof {w["canonical_proven_runs"]}/{w["compared_runs"]}.')
    lines += ['','**No global optimality claim. No overlapping projected window cost summation.**','']
    path=output/'stage2b_report.md'
    path.write_text('\n'.join(lines),encoding='utf-8') if not path.exists() else (_ for _ in ()).throw(FileExistsError('Report already exists'))
    print(json.dumps({'status':result['status'],'recommendation':recommendation,'json':str(output/'stage2b_report.json'),
                      'markdown':str(path)},ensure_ascii=False,indent=2))
    return 0 if result['status']=='COMPLETE_DIAGNOSTIC' else 2


def main(argv=None):
    p=argparse.ArgumentParser(description='S5 Stage2B full-pipeline repeatability and budget-sensitivity audit')
    p.add_argument('--mode',choices=('run','analyze'),required=True)
    p.add_argument('--config',type=Path)
    p.add_argument('--study-root',type=Path,required=True)
    p.add_argument('--report-root',type=Path,help='Separate output directory for reanalysis; never overwrites the study')
    p.add_argument('--call-ids-file',type=Path)
    p.add_argument('--budget',type=int,action='append',default=[])
    p.add_argument('--repetitions',type=int,default=20)
    p.add_argument('--tie-break-mode',choices=MODES,default='CANONICAL_ETB_BERTH_V1')
    p.add_argument('--candidates-per-pair',type=int,default=6)
    p.add_argument('--total-candidates',type=int,default=240)
    p.add_argument('--improvement-rounds',type=int,default=2)
    p.add_argument('--execution-timeout',type=int,default=0,help='Per S5 run subprocess timeout (0 => auto)')
    p.add_argument('--replay-timeout',type=int,default=300)
    args=p.parse_args(argv)
    if args.replay_timeout<1 or args.replay_timeout>3600 or args.execution_timeout<0:
        p.error('Invalid subprocess timeout')
    try:
        if args.mode=='run':
            return mode_run(args)
        return mode_analyze(args)
    except (ValueError,FileExistsError,OSError,KeyError,TypeError) as e:
        print(json.dumps({'status':'BLOCKED','error_code':type(e).__name__,'reason':str(e)},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=='__main__':sys.exit(main())
