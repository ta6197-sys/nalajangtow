#!/usr/bin/env python3
"""V5 Stage-1 reproducibility diagnostics; additive, no optimizer/DB writes.

Modes:
  run        20 isolated Stage-4 S5 Rolling runs, independent replay per run
  initial    20 pure initial-candidate generations, no CP-SAT
  frozen     20 isolated integrated CP-SAT solves on one frozen candidate domain
  analyze    compare all observed evidence, write JSON/CSV/Markdown
  _frozen_one internal isolated subprocess worker
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from datetime import datetime, timedelta
from hashlib import sha256

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.daily_planning.integrated_v3.v5_repeatability import (  # noqa: E402
    VERSION, canonical, digest, compare_runs, compare_series, read_run, read_window
)

EXPECTED_POLICY = 'V5_USER_ETU_SWITCHING_20261112_V1'
DEFAULT_REPEATS = 20


def write_new(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        f.write('\n')


def read(path: Path):
    if path.is_symlink():
        raise ValueError('Refusing symlinked diagnostic input: ' + str(path))
    return json.loads(path.read_text(encoding='utf-8'))


def git_head() -> dict:
    def command(*args):
        cp = subprocess.run(['git', *args], cwd=REPO, capture_output=True, text=True, check=False)
        return cp.stdout.strip() if cp.returncode == 0 else None
    return {'sha': command('rev-parse', 'HEAD'), 'branch': command('branch', '--show-current'),
            'status_porcelain': command('status', '--porcelain')}


def source_and_scope(config_file: Path, explicit_ids: Path | None):
    from src.daily_planning.integrated_v3.v5_scale import load_execution_config, preflight_sources, select_scope
    from src.daily_planning.physical_scheduling.v5_user_rules import VERSION as RULE_VERSION
    config = load_execution_config(config_file)
    if config['policy'].get('schema_version') != RULE_VERSION or RULE_VERSION != EXPECTED_POLICY:
        raise ValueError('Wrong policy: use the confirmed 2026-11-12 V5 user policy')
    snapshot, preflight = preflight_sources(config)
    if not preflight.get('data_preflight_pass'):
        raise ValueError('Original input V5 preflight failed')
    fixed_count = sum(c.get('fixed_etb_is_hard') is True for c in snapshot['calls'])
    if (len(snapshot['calls']), len(snapshot['berths']), len(snapshot['tanks']), fixed_count) != (140, 11, 3, 15):
        raise ValueError('Unexpected V5 source cardinalities')
    ids = read(explicit_ids) if explicit_ids else None
    scope = select_scope(snapshot, 5, ids)
    if len(scope) != 5:
        raise ValueError('S5 must have five calls')
    return config, snapshot, preflight, scope


def ensure_safe_root(root: Path, source: Path) -> None:
    root, source = root.resolve(), source.resolve()
    if root == source or source in root.parents:
        raise ValueError('Diagnostic output must not be placed inside source data')
    if root == REPO.resolve():
        raise ValueError('Refusing repository root as output')


def require_meta(root: Path, config_file: Path):
    meta = read(root / 'study_meta.json')
    if meta.get('version') != VERSION or meta.get('config_path') != str(config_file.resolve()):
        raise ValueError('Study/config identity differs; use exactly the original config')
    config, snapshot, preflight, scope = source_and_scope(config_file, root / 'scope_call_ids.json')
    if (digest(snapshot) != meta['source_hash'] or digest(config['policy']) != meta['policy_hash']
        or digest(config['pilotage_context']) != meta['pilotage_context_hash']
        or sha256(Path(config['pilotage_context']['daylight_path']).read_bytes()).hexdigest() != meta['daylight_sha256']
        or digest({'pricing_policy':config['pricing_policy'], 'clock_policy':config['clock_policy'],
                   'settlement_records':config['settlement_records']}) != meta['settlement_context_hash']):
        raise ValueError('Source/policy/daylight/pricing changed since first run; new study folder required')
    if scope != meta['scope_call_ids']:
        raise ValueError('Selected scope changed')
    return meta, config, snapshot, preflight, scope


def run_command(command, *, timeout_seconds, cwd=REPO):
    started = time.monotonic()
    try:
        done = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                              check=False, timeout=timeout_seconds)
        return {'exit_code': done.returncode, 'elapsed_seconds': time.monotonic()-started,
                'stdout_tail': done.stdout[-12000:], 'stderr_tail': done.stderr[-6000:]}
    except subprocess.TimeoutExpired as exc:
        return {'exit_code': None, 'elapsed_seconds': time.monotonic()-started,
                'error': 'PROCESS_TIMEOUT', 'stdout_tail': str(exc.stdout or '')[-2000:],
                'stderr_tail': str(exc.stderr or '')[-2000:]}
    except OSError as exc:
        return {'exit_code': None, 'elapsed_seconds': time.monotonic()-started,
                'error': type(exc).__name__+': '+str(exc)}


def config_policy_version(config_file: Path):
    from src.daily_planning.integrated_v3.v5_scale import load_execution_config
    return load_execution_config(config_file)['policy']


def create_study(args, config_file: Path, root: Path):
    config, snapshot, preflight, scope = source_and_scope(config_file, args.call_ids_file)
    ensure_safe_root(root, config['data_dir'])
    if root.exists():
        raise FileExistsError('Study root already exists. Never overwrite historical evidence: ' + str(root))
    root.mkdir(parents=True)
    write_new(root / 'scope_call_ids.json', scope)
    import ortools
    meta = {'version': VERSION, 'started_at': datetime.now().astimezone().isoformat(),
            'config_path': str(config_file.resolve()), 'source_hash': digest(snapshot),
            'policy_hash': digest(config['policy']), 'policy_version': EXPECTED_POLICY,
            'pilotage_context_hash':digest(config['pilotage_context']),
            'daylight_sha256':sha256(Path(config['pilotage_context']['daylight_path']).read_bytes()).hexdigest(),
            'settlement_context_hash':digest({'pricing_policy':config['pricing_policy'], 'clock_policy':config['clock_policy'],
                                              'settlement_records':config['settlement_records']}),
            'scope_call_ids': scope, 'as_of_at': config['as_of_at'], 'source_count': len(snapshot['calls']),
            'berth_count': len(snapshot['berths']), 'tank_count': len(snapshot['tanks']),
            'fixed_etb_count': sum(c.get('fixed_etb_is_hard') is True for c in snapshot['calls']),
            'preflight_status': preflight.get('status'), 'preflight_warning_count': len(preflight.get('warnings', [])),
            'python': platform.python_version(), 'ortools': ortools.__version__,
            'platform': platform.platform(), 'git': git_head(),
            'repetitions': args.repetitions, 'seconds_per_window': args.seconds_per_window,
            'candidates_per_pair': args.candidates_per_pair,
            'total_candidates': args.total_candidates, 'improvement_rounds': args.improvement_rounds,
            'scope': 'S5_5_CALLS_2_WINDOWS_NOT_ALL_140'}
    write_new(root / 'study_meta.json', meta)
    return meta


def mode_run(args, root: Path, config_file: Path) -> int:
    meta = create_study(args, config_file, root)
    output = root / 'runs'
    output.mkdir()
    records = []
    for i in range(1, args.repetitions+1):
        before = {p.name for p in output.iterdir() if p.is_dir()}
        cmd = [sys.executable, str(REPO/'scripts/v5_full/run_stage.py'), '--config', str(config_file),
               '--gate', 'S5', '--output-root', str(output), '--seconds-per-window', str(args.seconds_per_window),
               '--candidates-per-pair', str(args.candidates_per_pair), '--total-candidates', str(args.total_candidates),
               '--improvement-rounds', str(args.improvement_rounds), '--call-ids-file', str(root/'scope_call_ids.json')]
        proc = run_command(cmd, timeout_seconds=max(120, int(args.seconds_per_window*2.6)+120))
        after = {p.name for p in output.iterdir() if p.is_dir()}
        added = sorted(after-before)
        record = {'iteration': i, 'command': cmd, 'process': proc, 'new_run_ids': added,
                  'status': 'NOT_VERIFIED', 'independent_replay': 'NOT_RUN'}
        if len(added) == 1:
            rid = added[0]
            record['run_id'] = rid
            try:
                report = read(output / rid / 'execution_report.json')
                record['status'] = report.get('status')
                record['validated_window_count'] = report.get('validated_window_count')
                record['all_scope_calls_completed'] = report.get('all_scope_calls_completed')
                if report.get('source_fingerprint') != meta['source_hash']:
                    record['error'] = 'SOURCE_FINGERPRINT_MISMATCH'
                elif digest(config_policy_version(config_file)) != meta['policy_hash']:
                    record['error'] = 'POLICY_CHANGED_DURING_STUDY'
                elif report.get('status') == 'REQUESTED_WINDOWS_VALIDATED' and proc.get('exit_code') == 0:
                    verify = [sys.executable, str(REPO/'scripts/v5_full/verify_resume.py'), '--config', str(config_file),
                              '--root', str(output), '--run-id', rid]
                    checked = run_command(verify, timeout_seconds=max(120,int(args.seconds_per_window*4)+120))
                    record['verification_process'] = checked
                    try:
                        v = json.loads(checked.get('stdout_tail', ''))
                        record['independent_replay'] = ('PASS' if checked['exit_code']==0 and v.get('independent_replay')=='PASS'
                                                        and v.get('status')=='REQUESTED_WINDOWS_VALIDATED' else 'FAIL')
                    except (ValueError, TypeError):
                        record['independent_replay'] = 'FAIL_UNPARSEABLE'
                else:
                    record['independent_replay'] = 'BLOCKED_INCOMPLETE_RUN'
            except (OSError, ValueError, KeyError) as exc:
                record['error'] = type(exc).__name__+': '+str(exc)
        else:
            record['error'] = 'Expected exactly one newly created run directory'
        write_new(root / 'records' / ('run_%03d.json' % i), record)
        records.append({'iteration': i, 'run_id': record.get('run_id'), 'status': record['status'],
                        'independent_replay': record['independent_replay']})
        print('C %02d/%02d %s replay=%s' % (i, args.repetitions, record['status'], record['independent_replay']), flush=True)
    write_new(root/'run_index.json', records)
    return 0 if all(r['status']=='REQUESTED_WINDOWS_VALIDATED' and r['independent_replay']=='PASS' for r in records) else 2


def baseline_checkpoint(root: Path):
    from src.daily_planning.integrated_v3.v5_run_review import load_checkpoints
    records = read(root/'run_index.json')
    for item in records:
        if item.get('independent_replay') != 'PASS':
            continue
        run_id = item['run_id']; directory = root/'runs'/run_id
        manifest, checkpoints = load_checkpoints(directory)
        if len(checkpoints) == 2 and checkpoints[0].get('selected_schedule'):
            return manifest, checkpoints, run_id
    raise ValueError('No complete independently replayed baseline for first-window A/B tests')


def mode_initial(args, root: Path, config_file: Path):
    meta, config, snap, _preflight, scope = require_meta(root,config_file)
    manifest, checkpoints, _rid = baseline_checkpoint(root)
    window = checkpoints[0]
    selected = window['summary']['selected_call_ids']
    if not selected:
        raise ValueError('First validated window has no selected calls')
    from src.daily_planning.integrated_v3.v5_physical import _verified_snapshot
    from src.daily_planning.integrated_v3.v5_pilotage import prepare_pilotage_policy
    from src.daily_planning.integrated_input.v5 import eligible_berth_ids
    from src.daily_planning.integrated_v3.v5_candidates import CandidatePool
    from src.daily_planning.integrated_input.provenance import canonical as repo_canonical, digest as repo_digest
    work = _verified_snapshot(snap, config['data_dir'])
    calls = {c['call_id']:c for c in work['calls']}
    eligible = {cid:eligible_berth_ids(calls[cid],work['berths'],work['tanks']) for cid in selected}
    records = []
    for i in range(1,meta['repetitions']+1):
        try:
            deadline = time.perf_counter()+meta['seconds_per_window']
            policy, issue = prepare_pilotage_policy(work,selected,eligible,config['policy'],config['pilotage_context'],
                                                     window['as_of_at'],{},None)
            if issue:
                raise ValueError('Pilotage policy unresolved: '+str(issue))
            pool=CandidatePool(work,{cid:calls[cid] for cid in selected},eligible,policy,window['as_of_at'],
                               commitments=(),pair_limit=meta['candidates_per_pair'],total_limit=meta['total_candidates'],deadline=deadline)
            pool.initial(); dom=pool.domain()
            record={'iteration':i,'status':'PASS','domain_hash':repo_digest(repo_canonical(dom).encode()),
                    'candidate_count':pool.count(),'rejections':len(pool.rejected)}
            if i == 1:
                write_new(root/'initial_domain_baseline.json',dom)
        except Exception as exc:
            record={'iteration':i,'status':'FAIL','error':type(exc).__name__+': '+str(exc)}
        write_new(root/'initial' / ('initial_%03d.json' % i),record)
        records.append(record)
        print('A %02d/%02d %s' % (i,meta['repetitions'],record['status']),flush=True)
    return 0 if all(r['status']=='PASS' for r in records) else 2


def frozen_domain_file(root: Path):
    manifest, checkpoints, rid=baseline_checkpoint(root)
    cp=checkpoints[0]
    opt=((cp.get('candidate') or {}).get('optimizer_result') or {})
    domain=opt.get('candidates_by_call_and_berth')
    if not isinstance(domain,dict) or not domain or not all(isinstance(v,dict) for v in domain.values()):
        raise ValueError('No validated frozen candidate domain in first-window optimizer checkpoint')
    if set(domain) != set(cp['summary']['selected_call_ids']):
        raise ValueError('Frozen domain call set differs from selected first-window scope')
    return {'base_run_id':rid, 'window_id':1, 'as_of_at':cp['as_of_at'], 'call_ids':sorted(domain),
            'domain':domain, 'domain_hash':digest(domain),
            'baseline_window_cost_cents':cp['summary']['window_demurrage_cents'],
            'source_fingerprint':manifest['config']['source_fingerprint']}


def frozen_once(root:Path, config_file:Path, iteration:int, limit:float):
    meta, config, snap, _preflight, _scope = require_meta(root,config_file)
    frozen=read(root/'frozen_domain.json')
    if frozen['source_fingerprint']!=meta['source_hash'] or digest(frozen['domain'])!=frozen['domain_hash']:
        raise ValueError('Frozen domain/source fingerprint mismatch')
    from src.daily_planning.integrated_v3.v5_physical import schedule_v5
    count=sum(len(times) for byberth in frozen['domain'].values() for times in byberth.values())
    max_per_pair=max(len(times) for byberth in frozen['domain'].values() for times in byberth.values())
    if not count or max_per_pair>24 or count>12000:
        raise ValueError('Frozen domain exceeds supported limits')
    result=schedule_v5(snap,data_dir=config['data_dir'],call_ids=frozen['call_ids'],as_of_at=frozen['as_of_at'],
                       policy=config['policy'],pilotage_context=config['pilotage_context'],time_limit_seconds=limit,
                       candidate_generation_mode='AUTO_ADAPTIVE_CANDIDATES',frozen_candidate_domain=frozen['domain'],
                       max_expansion_rounds=0,max_candidates_per_pair=max_per_pair,max_total_candidates=count,
                       candidate_improvement_mode='NONE',objective_mode='MIN_TOTAL_DEMURRAGE',
                       inventory_constraint_mode='EXACT_EVENT_BALANCE_V1',
                       pricing_policy=config['pricing_policy'],clock_policy=config['clock_policy'],
                       settlement_records=config['settlement_records'])
    ok=(result.get('status')=='PHYSICAL_AND_INVENTORY_VALID'
        and result.get('combined_validation')=='PHYSICAL_AND_INVENTORY_VALID'
        and result.get('settlement_validation')=='SETTLED')
    selected={r['call_id']:{'berth_id':r['berth_id'],'etb_at':r['etb_at']} for r in result.get('selected_schedule',[])}
    return {'iteration':iteration,'status':'PASS' if ok else 'FAIL','optimizer_status':result.get('status'),
            'solver_status':result.get('solver_status'), 'independent_physical_status':(result.get('physical_validation') or {}).get('status')
            if isinstance(result.get('physical_validation'),dict) else result.get('physical_validation'),
            'inventory_status':result.get('inventory_validation_status') or (result.get('inventory_validation') or {}).get('status') if isinstance(result.get('inventory_validation'),dict) else result.get('inventory_validation'),
            'settlement_status':result.get('settlement_validation'),'cost_cents':result.get('total_projected_demurrage_cents'),
            'schedule_hash':digest(selected) if selected else None,'selected_schedule':selected,
            'domain_hash':frozen['domain_hash'], 'candidate_count':count,'issues':result.get('issues',[])[:15],
            'runtime_seconds':result.get('runtime_seconds'),'scope':'FIRST_ROLLING_WINDOW_FROZEN_DOMAIN_ONLY'}


def mode_frozen(args,root:Path,config_file:Path):
    meta, _, _, _, _=require_meta(root,config_file)
    frozen=frozen_domain_file(root)
    write_new(root/'frozen_domain.json',frozen)
    results=[]
    for i in range(1,meta['repetitions']+1):
        cmd=[sys.executable,str(Path(__file__).resolve()),'--mode','_frozen_one',
             '--config',str(config_file),'--study-root',str(root),'--iteration',str(i),
             '--seconds-per-window',str(meta['seconds_per_window'])]
        result=run_command(cmd,timeout_seconds=max(120,int(args.seconds_per_window*2)+90))
        path=root/'frozen'/('frozen_%03d.json'%i)
        if not path.is_file():
            write_new(path,{'iteration':i,'status':'FAIL','process':result})
        record=read(path)
        results.append(record)
        print('B %02d/%02d %s' % (i,meta['repetitions'],record['status']),flush=True)
    return 0 if all(r['status']=='PASS' for r in results) else 2


def mode_analyze(root:Path):
    from src.daily_planning.integrated_v3.v5_run_review import load_checkpoints
    meta=read(root/'study_meta.json')
    run_index=read(root/'run_index.json')
    runs=[]
    errors=[]
    for item in run_index:
        rid=item.get('run_id')
        record=read(root/'records'/('run_%03d.json'%item['iteration']))
        if not rid:
            errors.append({'iteration':item['iteration'],'error':record.get('error','MISSING_RUN_ID')})
            runs.append({'run_id': 'missing-%d'%item['iteration'],'fully_validated_windows':False,'independent_replay':'NOT_RUN'})
            continue
        directory=root/'runs'/rid
        try:
            report=read(directory/'execution_report.json')
            manifest,checkpoints=load_checkpoints(directory)
            if manifest['config']['source_fingerprint']!=meta['source_hash']:
                raise ValueError('Manifest source does not match study')
            entry=read_run(report,checkpoints,structural_valid=True,
                           replay_status=record.get('independent_replay'),
                           policy_fingerprint=manifest['config'].get('policy_fingerprint'))
            runs.append(entry)
        except Exception as exc:
            errors.append({'run_id':rid,'error':type(exc).__name__+': '+str(exc)})
            runs.append({'run_id':rid,'fully_validated_windows':False,
                         'independent_replay':record.get('independent_replay')})
    c=compare_runs(runs,expected_repetitions=meta['repetitions'])
    c['checkpoint_errors']=errors
    a=[read(p) for p in sorted((root/'initial').glob('initial_*.json'))] if (root/'initial').exists() else []
    b=[read(p) for p in sorted((root/'frozen').glob('frozen_*.json'))] if (root/'frozen').exists() else []
    a_report=compare_series('A_INITIAL_CANDIDATE_ONLY',a,count=meta['repetitions'])
    b_report=compare_series('B_FROZEN_FIRST_WINDOW_INTEGRATED_SOLVE',b,count=meta['repetitions'])
    result={'version':VERSION,'study':{'git':meta['git'],'python':meta['python'],'ortools':meta['ortools'],
                                     'source_hash':meta['source_hash'],'policy_hash':meta['policy_hash'],
                                     'pilotage_context_hash':meta['pilotage_context_hash'],
                                     'daylight_sha256':meta['daylight_sha256'],
                                     'scope_call_ids':meta['scope_call_ids'],'repetitions':meta['repetitions'],
                                     'seconds_per_window':meta['seconds_per_window']},
            'A':a_report,'B':b_report,'C':c,
            'interpretation_rules':{
                'A_differs':'Initial deterministic candidate enumeration or clock/cap variability',
                'B_same_domain_cost_differs':'Potential solver stop/time budget influence; inspect proof and stops',
                'B_same_cost_schedule_differs':'Possible alternate minimum-cost schedule if all solves prove optimal',
                'C_initial_equal_final_differs':'Cost-aware adaptive expansion or time budget/selected incumbent variation',
                'C_equal_cost_schedule_differs':'Schedule tie or insufficient solver proof; no lexicographic stability objective currently',
                'C_run_invalid':'Do not infer repeatability from partially validated or failed runs'},
            'limitations':['No new objective or tie-break was installed','No 140-vessel/global-optimality claims',
                           'First-window frozen re-solve excludes Rolling later-window carryovers',
                           'Window projected costs are NOT additive','S5 call completion is distinct from validated windows']}
    write_new(root/'repeatability_report.json',result)
    with (root/'repeatability_etb_rows.csv').open('x',newline='',encoding='utf-8-sig') as f:
        writer=csv.writer(f);writer.writerow(['run_id','window_id','call_id','berth_id','etb_utc','cost_cents','first_domain_hash','final_domain_hash','solver_status','final_domain_optimal'])
        for entry in runs:
            for w in entry.get('windows',[]):
                for cid,row in sorted(w['schedule'].items()):
                    writer.writerow([entry['run_id'],w['window_id'],cid,row['berth_id'],row['etb_utc'],w['cost_cents'],
                                     w['first_domain_hash'],w['final_domain_hash'],w['solver_status'],w['final_domain_optimality_proven']])
    md=['# Docktimize V5 Repeatability Audit','',f"- Source SHA256: `{meta['source_hash']}`",
        f"- Policy SHA256: `{meta['policy_hash']}`",f"- Git commit: `{meta['git']['sha']}`",
        f"- Scope: S5 / 5 calls / 2 rolling windows; repetitions: {meta['repetitions']}",
        f"- A initial candidates: **{a_report['status']}**",
        f"- B frozen first-window solve: **{b_report['status']}**",
        f"- C complete rolling runs: **{c['status']}**",'',
        '## Window-level evidence','']
    for w in c['windows']:
        md += [f"### Window {w['window_id']}",
               f"- Max ETB span: **{w['max_etb_span_minutes']} minutes**",
               f"- Unique initial/final domains: {w['initial_domain_unique_hashes']} / {w['final_domain_unique_hashes']}",
               f"- Unique schedules: {w['schedule_unique_hashes']}",
               f"- Min/max projected cost cents: {w['cost_cents_min_max']}",
               f"- Signals: {', '.join(w['diagnostic_flags'])}",'']
    md+=['## Guardrails','','All conclusions are restricted to the five-call, two-window synthetic experiment.',
         'A completed pair of rolling windows does not mean all 5 calls have completed.',
         'Do not sum overlapping window projected costs as cumulative settlement.',
         'No schedule stabilization objective was introduced by this audit.','']
    (root/'repeatability_report.md').write_text('\n'.join(md),encoding='utf-8')
    print(json.dumps({'A':a_report['status'],'B':b_report['status'],'C':c['status'],
                      'report':str(root/'repeatability_report.json')},indent=2))
    return 0 if a_report['status']=='REPEATED' and b_report['status']=='REPEATED' and c['status']=='REPEATABLE_IN_TESTED_WINDOWS' else 2


def main(argv=None):
    p=argparse.ArgumentParser(description='Read-only V5 S5 candidate / frozen-solver / Rolling repeatability audit')
    p.add_argument('--mode',required=True,choices=['run','initial','frozen','analyze','_frozen_one'])
    p.add_argument('--config',type=Path)
    p.add_argument('--study-root',required=True,type=Path)
    p.add_argument('--call-ids-file',type=Path)
    p.add_argument('--repetitions',type=int,default=DEFAULT_REPEATS)
    p.add_argument('--seconds-per-window',type=float,default=60)
    p.add_argument('--candidates-per-pair',type=int,default=6)
    p.add_argument('--total-candidates',type=int,default=240)
    p.add_argument('--improvement-rounds',type=int,default=2)
    p.add_argument('--iteration',type=int,default=0,help=argparse.SUPPRESS)
    args=p.parse_args(argv)
    if not 2<=args.repetitions<=50 or not 1<=args.seconds_per_window<=1800 or not 1<=args.candidates_per_pair<=24 or not 1<=args.total_candidates<=12000 or not 1<=args.improvement_rounds<=2:
        p.error('Invalid study limits')
    root=args.study_root.expanduser().resolve()
    if args.mode!='analyze' and args.config is None:
        p.error('--config required for run/initial/frozen')
    config_file=args.config.expanduser().resolve() if args.config else None
    try:
        if args.mode=='run':return mode_run(args,root,config_file)
        if args.mode=='initial':return mode_initial(args,root,config_file)
        if args.mode=='frozen':return mode_frozen(args,root,config_file)
        if args.mode=='analyze':return mode_analyze(root)
        if args.mode=='_frozen_one':
            try:
                result=frozen_once(root,config_file,args.iteration,args.seconds_per_window)
            except Exception as exc:
                result={'iteration':args.iteration,'status':'FAIL','error':type(exc).__name__+': '+str(exc)}
            write_new(root/'frozen'/('frozen_%03d.json'%args.iteration),result)
            print(json.dumps({'iteration':args.iteration,'status':result['status']}))
            return 0 if result['status']=='PASS' else 2
    except (OSError,ValueError,KeyError,ImportError,RuntimeError) as exc:
        print(json.dumps({'status':'BLOCKED','type':type(exc).__name__,'reason':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
