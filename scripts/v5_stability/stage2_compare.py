#!/usr/bin/env python3
"""Read-only, source/policy-matched S5 baseline vs opt-in canonical run audit.

This is NOT an independent physical/inventory/settlement validation. Always run
scripts/v5_full/verify_resume.py on BOTH runs before accepting the comparison.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from src.daily_planning.integrated_v3.v5_run_review import load_checkpoints, resolve_run, load_json
from src.daily_planning.integrated_v3.v5_repeatability import read_window


def compare_checkpoint_pairs(baseline_manifest, baseline_windows, stable_manifest, stable_windows):
    bc, sc = baseline_manifest['config'], stable_manifest['config']
    keys = ('source_fingerprint','policy_fingerprint','start_at','scope','windows',
            'time_limit_seconds','pricing_policy','clock_policy','max_candidates_per_pair',
            'max_total_candidates','max_improvement_rounds')
    mismatch = [name for name in keys if bc.get(name) != sc.get(name)]
    if mismatch:
        raise ValueError('Runs have different evidence contexts: '+', '.join(mismatch))
    if bc.get('tie_break_mode','NONE') != 'NONE' or sc.get('tie_break_mode') != 'CANONICAL_ETB_BERTH_V1':
        raise ValueError('Expected legacy baseline and opt-in stable mode')
    if len(baseline_windows) != len(stable_windows) or len(baseline_windows) != bc['windows']:
        raise ValueError('Window counts differ')
    comparisons = []
    for a,b in zip(baseline_windows,stable_windows):
        x,y = read_window(a),read_window(b)
        if x['window_id'] != y['window_id']:
            raise ValueError('Window ID mismatch')
        ids = sorted(set(x['schedule']) | set(y['schedule']))
        differences=[]
        for cid in ids:
            ar=x['schedule'].get(cid);br=y['schedule'].get(cid)
            if ar is None or br is None:
                differences.append({'call_id':cid,'issue':'SCHEDULE_MEMBERSHIP_DIFFERENCE'})
                continue
            delta=abs((datetime.fromisoformat(ar['etb_utc'])-datetime.fromisoformat(br['etb_utc'])).total_seconds()) / 60
            if delta or ar['berth_id']!=br['berth_id']:
                differences.append({'call_id':cid,'etb_difference_minutes':delta,
                                    'berth_changed':ar['berth_id']!=br['berth_id']})
        optimizer=(b.get('candidate') or {}).get('optimizer_result') or {}
        comparisons.append({
            'window_id':x['window_id'], 'baseline_cost_cents':x['cost_cents'],
            'stable_cost_cents':y['cost_cents'],
            'cost_delta_cents':y['cost_cents']-x['cost_cents'],
            'same_final_candidate_domain':x['final_domain_hash']==y['final_domain_hash'],
            'same_initial_candidate_domain':x['first_domain_hash']==y['first_domain_hash'],
            'same_schedule':x['schedule_hash']==y['schedule_hash'],
            'tie_break':optimizer.get('schedule_tiebreak'),
            'primary_solver_status':y['solver_status'],
            'final_domain_optimality_proven':y['final_domain_optimality_proven'],
            'differences':differences})
    return {'scope':'S5_TWO_WINDOW_SYNTHETIC_COMPARISON_ONLY',
            'independent_replay':'NOT_RUN_BY_THIS_TOOL',
            'global_optimality_proven':False,'simulation_only':True,
            'status':'COMPARED_NOT_INDEPENDENTLY_VERIFIED',
            'windows':comparisons}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--baseline-root',type=Path,required=True)
    ap.add_argument('--baseline-run-id',required=True)
    ap.add_argument('--stable-root',type=Path,required=True)
    ap.add_argument('--stable-run-id',required=True)
    ap.add_argument('--report',type=Path,required=True)
    a=ap.parse_args()
    a_dir=resolve_run(a.baseline_root,a.baseline_run_id)
    b_dir=resolve_run(a.stable_root,a.stable_run_id)
    ma,wa=load_checkpoints(a_dir);mb,wb=load_checkpoints(b_dir)
    ra=load_json(a_dir/'execution_report.json');rb=load_json(b_dir/'execution_report.json')
    if ra.get('status')!='REQUESTED_WINDOWS_VALIDATED' or rb.get('status')!='REQUESTED_WINDOWS_VALIDATED':
        raise ValueError('Incomplete rolling runs are not comparable as validated evidence')
    compared=compare_checkpoint_pairs(ma,wa,mb,wb)
    compared.update(baseline_run_id=a.baseline_run_id,stable_run_id=a.stable_run_id)
    a.report.parent.mkdir(parents=True,exist_ok=True)
    with a.report.open('x',encoding='utf-8') as f:
        json.dump(compared,f,ensure_ascii=False,indent=2,sort_keys=True)
        f.write('\n')
    print(json.dumps(compared,ensure_ascii=False,indent=2,sort_keys=True))


if __name__=='__main__':main()
