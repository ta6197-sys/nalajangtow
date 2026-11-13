"""Read-only independent Stage-4 resume replay for a *complete* saved run.

Calls the existing run_v5_rolling(resume=True) only after proving all requested
window checkpoints exist; therefore it cannot create new windows.
"""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.daily_planning.integrated_v3.v5_run_review import load_checkpoints, resolve_run
from src.daily_planning.integrated_v3.v5_scale import load_execution_config, preflight_sources
from src.daily_planning.integrated_v3.v5_rolling import run_v5_rolling


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True, type=Path)
    p.add_argument('--root', type=Path, default=ROOT/'results'/'v5_preview_runs')
    p.add_argument('--run-id', required=True)
    args = p.parse_args()
    folder = resolve_run(args.root,args.run_id)
    manifest, checkpoints = load_checkpoints(folder)
    cfg = manifest['config']
    if len(checkpoints) != cfg.get('windows'):
        print(json.dumps({'status':'BLOCKED','reason':'Incomplete run; replay could create new checkpoints'}))
        return 2
    config = load_execution_config(args.config)
    snapshot, preflight = preflight_sources(config)
    # Exact manifest fingerprint checks are performed inside run_v5_rolling.
    # Use the original recorded scope, candidate settings and pricing policy.
    try:
        outcome = run_v5_rolling(snapshot, data_dir=config['data_dir'],policy=config['policy'],
            pilotage_context=config['pilotage_context'],as_of_at=config['as_of_at'],
            output_dir=folder,call_ids=cfg['scope'],windows=cfg['windows'],
            time_limit_seconds=cfg['time_limit_seconds'],
            max_cost_candidates=cfg['max_cost_candidates'],
            pricing_policy=config['pricing_policy'],clock_policy=config['clock_policy'],
            settlement_records=config['settlement_records'],
            approved_commitments=cfg.get('approved_commitments',[]),
            override_events=cfg.get('override_events',{}),
            resume=True, rolling_optimizer_mode='INTEGRATED_COST_AWARE_V1',
            max_improvement_rounds=cfg['max_improvement_rounds'],
            max_candidates_per_pair=cfg['max_candidates_per_pair'],
            max_total_candidates=cfg['max_total_candidates'],
            **({'tie_break_mode':cfg['tie_break_mode']} if cfg.get('tie_break_mode','NONE')!='NONE' else {}),
            **({'inventory_policy_version':config['inventory_policy_version']} if config.get('inventory_policy_version') is not None else {}),
            **({'inventory_risk_policy_version':config['inventory_risk_policy_version']} if config.get('inventory_risk_policy_version') is not None else {}),
            **({'deferral_policy_version':config['deferral_policy_version'],'max_deferral_probes':config['max_deferral_probes']} if config.get('deferral_policy_version') is not None else {}))
        result = {'status':outcome['status'],'validated_window_count':outcome['validated_window_count'],
                  'resume_status':outcome['resume_status'],
                  'independent_replay': 'PASS' if outcome['status']=='REQUESTED_WINDOWS_VALIDATED'
                  else 'FAILED','simulation_only':True}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result['independent_replay']=='PASS' else 2
    except Exception as exc:
        print(json.dumps({'status':'REPLAY_FAILED','exception':type(exc).__name__,
                          'reason':str(exc)},ensure_ascii=False))
        return 2


if __name__=='__main__':sys.exit(main())
