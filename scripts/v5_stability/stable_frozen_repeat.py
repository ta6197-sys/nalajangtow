#!/usr/bin/env python3
"""Opt-in Stage 2A fixed-domain 20x verification, without changing Stage 1 evidence.

Uses a pre-existing independently validated Stage 1 S5 study and exactly its
first-window candidate domain. Each trial is a fresh Python process. The tool
runs no DB, migrations or synthetic data creation. No file is overwritten.
"""
from __future__ import annotations

import argparse
import csv
import json
import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def iso_utc(value):
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError('Timezone-aware ETB required')
    return instant.astimezone(timezone.utc).isoformat(timespec='microseconds')


def read(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Refusing symlinked audit evidence')
    return json.loads(path.read_text(encoding='utf-8'))


def write_new(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')

MODE = 'CANONICAL_ETB_BERTH_V1'


def verify_frozen(study: Path, config: Path):
    from scripts.v5_stability.repeatability_audit import require_meta, frozen_domain_file
    meta, cfg, snap, _, _ = require_meta(study, config)
    verified = frozen_domain_file(study)  # requires an independently replayed source-verified Stage-1 checkpoint
    frozen = read(study / 'frozen_domain.json') if (study / 'frozen_domain.json').is_file() else verified
    if digest(frozen.get('domain')) != digest(verified.get('domain')):
        raise ValueError('Stored frozen domain differs from verified Stage-1 baseline')
    if frozen.get('source_fingerprint') != meta['source_hash']:
        raise ValueError('Frozen candidate domain belongs to a different V5 snapshot')
    if not isinstance(frozen.get('domain'), dict) or digest(frozen['domain']) != frozen.get('domain_hash'):
        raise ValueError('Frozen domain fingerprint mismatch')
    if set(frozen['domain']) != set(frozen['call_ids']):
        raise ValueError('Frozen domain call set mismatch')
    if not frozen['call_ids']:
        raise ValueError('Empty first-window call set')
    return meta, cfg, snap, frozen


def _once(study: Path, config: Path, limit: float, number: int) -> dict:
    meta, cfg, snap, frozen = verify_frozen(study, config)
    from src.daily_planning.integrated_v3.v5_physical import schedule_v5
    count = sum(len(items) for by_berth in frozen['domain'].values() for items in by_berth.values())
    max_pair = max((len(items) for by_berth in frozen['domain'].values() for items in by_berth.values()), default=0)
    if not 1 <= max_pair <= 24 or not 1 <= count <= 12000:
        raise ValueError('Frozen domain outside existing V5 candidate limits')
    result = schedule_v5(
        snap, data_dir=cfg['data_dir'], call_ids=frozen['call_ids'],
        as_of_at=frozen['as_of_at'], policy=cfg['policy'],
        pilotage_context=cfg['pilotage_context'], time_limit_seconds=limit,
        candidate_generation_mode='AUTO_ADAPTIVE_CANDIDATES',
        frozen_candidate_domain=frozen['domain'], max_expansion_rounds=0,
        max_candidates_per_pair=max_pair, max_total_candidates=count,
        candidate_improvement_mode='NONE', objective_mode='MIN_TOTAL_DEMURRAGE',
        inventory_constraint_mode='EXACT_EVENT_BALANCE_V1',
        pricing_policy=cfg['pricing_policy'], clock_policy=cfg['clock_policy'],
        settlement_records=cfg['settlement_records'], tie_break_mode=MODE)
    rows = result.get('selected_schedule') or []
    selected = {r['call_id']: {
        'etb_utc': iso_utc(r['etb_at']), 'berth_id': r['berth_id'],
        'hose_connection_start_at': r.get('hose_connection_start_at')}
        for r in rows}
    physical = result.get('physical_validation') or {}
    physical_ok = isinstance(physical, dict) and physical.get('status') == 'VALID'
    inventory = result.get('inventory_validation') or {}
    inventory_ok = (inventory.get('status') in ('INVENTORY_VALID', 'VALID') if isinstance(inventory, dict)
                    else inventory in ('INVENTORY_VALID', 'VALID'))
    valid = (result.get('status') == 'PHYSICAL_AND_INVENTORY_VALID'
             and result.get('combined_validation') == 'PHYSICAL_AND_INVENTORY_VALID'
             and result.get('settlement_validation') == 'SETTLED'
             and physical_ok and inventory_ok and len(selected) == len(frozen['call_ids'])
             and type(result.get('total_projected_demurrage_cents')) is int)
    return {
        'iteration': number, 'status': 'VALIDATED' if valid else 'FAILED',
        'optimizer_status': result.get('status'), 'solver_status': result.get('solver_status'),
        'optimization_status': result.get('optimization_status'),
        'schedule_tiebreak': result.get('schedule_tiebreak'),
        'cost_cents': result.get('total_projected_demurrage_cents'),
        'schedule_hash': digest(selected) if selected else None,
        'selected_schedule': selected, 'candidate_domain_hash': frozen['domain_hash'],
        'primary_domain_optimality_proven': result.get('solver_status') == 'OPTIMAL',
        'physical_valid': physical_ok, 'inventory_valid': inventory_ok,
        'settlement_valid': result.get('settlement_validation') == 'SETTLED',
        'issues': (result.get('issues') or [])[:15], 'runtime_seconds': result.get('runtime_seconds'),
        'scope': 'FIRST_ROLLING_WINDOW_FROZEN_DOMAIN_ONLY',
        'simulation_only': True, 'operational_approval': False,
    }


def analyze_records(records: list[dict], repeats: int, frozen_hash: str) -> dict:
    valid = [r for r in records if r.get('status') == 'VALIDATED']
    proven = [r for r in valid if (r.get('schedule_tiebreak') or {}).get('status') == 'PROVEN_CANONICAL_SELECTION'
              and r.get('primary_domain_optimality_proven') is True and r.get('solver_status') == 'OPTIMAL']
    costs = {r['cost_cents'] for r in valid if type(r.get('cost_cents')) is int}
    schedules = {r['schedule_hash'] for r in valid if r.get('schedule_hash')}
    domains = {r.get('candidate_domain_hash') for r in records if r.get('candidate_domain_hash')}
    ids = sorted({cid for r in valid for cid in (r.get('selected_schedule') or {})})
    vessels = []
    for cid in ids:
        observed = [r['selected_schedule'][cid] for r in valid if cid in r['selected_schedule']]
        times = [datetime.fromisoformat(v['etb_utc']).timestamp() for v in observed]
        berths = sorted({v['berth_id'] for v in observed})
        vessels.append({'call_id': cid, 'observed_runs': len(observed), 'etb_span_minutes':
                        (max(times)-min(times))/60 if times else None,
                        'berth_changed_between_runs': len(berths) > 1, 'berths': berths})
    complete = len(records) == repeats and len(valid) == repeats
    is_same = (len(costs) == len(schedules) == len(domains) == 1 and domains == {frozen_hash}
               and all(v['etb_span_minutes'] == 0 and not v['berth_changed_between_runs'] for v in vessels))
    proof_complete = len(proven) == repeats
    if not complete:
        outcome = 'INCOMPLETE_OR_FAILED'
    elif not is_same:
        outcome = 'VARIATION_DETECTED'
    elif not proof_complete:
        outcome = 'OBSERVED_REPEATABLE_BUT_CANONICAL_NOT_PROVEN'
    else:
        outcome = 'PROVEN_REPEATABLE_IN_FIXED_DOMAIN'
    return {
        'status': outcome, 'requested': repeats, 'observed': len(records),
        'validated': len(valid), 'canonical_proven': len(proven),
        'unique_schedule_hashes': len(schedules), 'unique_costs': len(costs),
        'unique_candidate_domain_hashes': len(domains),
        'candidate_domain_hash': frozen_hash,
        'cost_min_max_cents': [min(costs), max(costs)] if costs else None,
        'max_etb_span_minutes': max((v['etb_span_minutes'] or 0 for v in vessels), default=None),
        'vessels': vessels,
        'primary_solver_statuses': sorted({str(r.get('solver_status')) for r in valid}),
        'secondary_statuses': sorted({str((r.get('schedule_tiebreak') or {}).get('status')) for r in valid}),
        'failed': [{'iteration':r.get('iteration'),'status':r.get('status'),'issues':r.get('issues', [])}
                   for r in records if r.get('status') != 'VALIDATED'],
        'limitations': ['First rolling window only; not full S5 vessel completion',
                        'Finite frozen ETB candidate domain; not continuous-time/global optimality',
                        'Full Rolling independent replay is a separate validation'],
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('run','_once'), default='run')
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--stage1-study-root', type=Path, required=True)
    p.add_argument('--output-root', type=Path, required=True)
    p.add_argument('--repetitions', type=int, default=20)
    p.add_argument('--seconds-per-window', type=float, default=120)
    p.add_argument('--iteration', type=int)
    a = p.parse_args(argv)
    study, config, output = a.stage1_study_root.resolve(), a.config.resolve(), a.output_root.resolve()
    if not 1 <= a.repetitions <= 100 or not 1 <= a.seconds_per_window <= 1800:
        p.error('Invalid repetitions or solver budget')
    if output == study or study in output.parents or output in study.parents:
        p.error('Output root must be separate from immutable Stage 1 evidence')
    if a.mode == '_once':
        if not a.iteration or not (output/'study_manifest.json').is_file():
            p.error('Missing initialized study or iteration')
        try:
            record = _once(study, config, a.seconds_per_window, a.iteration)
        except Exception as exc:
            record = {'iteration':a.iteration,'status':'FAILED','issues':[{'code':'EXECUTION_EXCEPTION',
                        'type':type(exc).__name__, 'reason':str(exc)}]}
        write_new(output/'trials'/f'trial_{a.iteration:03d}.json', record)
        return 0 if record.get('status') == 'VALIDATED' else 2
    if output.exists():
        p.error('Output root exists; historical evidence must never be overwritten')
    meta, cfg, snap, frozen = verify_frozen(study, config)
    if output == Path(cfg['data_dir']).resolve() or Path(cfg['data_dir']).resolve() in output.parents:
        p.error('Refusing to write audit output inside source data')
    output.mkdir(parents=True)
    write_new(output/'study_manifest.json', {
        'stage1_root':str(study), 'config':str(config), 'source_hash':meta['source_hash'],
        'policy_hash':meta['policy_hash'], 'frozen_hash':frozen['domain_hash'],
        'mode':MODE, 'repetitions':a.repetitions, 'seconds_per_window':a.seconds_per_window,
    })
    records = []
    for i in range(1,a.repetitions + 1):
        cmd=[sys.executable,str(Path(__file__).resolve()),'--mode','_once', '--config',str(config),
             '--stage1-study-root',str(study),'--output-root',str(output),'--iteration',str(i),
             '--seconds-per-window',str(a.seconds_per_window)]
        try:
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=a.seconds_per_window*2+120,
                                  check=False,cwd=ROOT)
        except subprocess.TimeoutExpired:
            result=None
        dest=output/'trials'/f'trial_{i:03d}.json'
        if not dest.is_file():
            write_new(dest, {'iteration':i,'status':'FAILED','issues':[{'code':'PROCESS_TIMEOUT_OR_EXIT',
                                'exit_code':result.returncode if result else None,
                                'stderr_tail':result.stderr[-1200:] if result else 'timeout'}]})
        record=read(dest)
        if result is not None and result.returncode != 0 and record.get('status') == 'VALIDATED':
            record = dict(record, status='FAILED', issues=[{'code':'NONZERO_PROCESS_EXIT'}])
        records.append(record)
        print(f'fixed-domain stable: {i}/{a.repetitions} {record["status"]}',flush=True)
    report=analyze_records(records,a.repetitions,frozen['domain_hash'])
    write_new(output/'repeatability_stage2a_report.json',report)
    with (output/'etb_per_vessel.csv').open('x',newline='',encoding='utf-8') as fh:
        writer=csv.DictWriter(fh,fieldnames=['iteration','call_id','berth_id','etb_utc'])
        writer.writeheader()
        for record in records:
            for cid,v in sorted((record.get('selected_schedule') or {}).items()):
                writer.writerow({'iteration':record['iteration'],'call_id':cid,
                                 'berth_id':v['berth_id'],'etb_utc':v['etb_utc']})
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['status']=='PROVEN_REPEATABLE_IN_FIXED_DOMAIN' else 2


if __name__=='__main__':
    raise SystemExit(main())
