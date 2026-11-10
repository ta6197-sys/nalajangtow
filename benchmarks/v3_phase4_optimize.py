"""Run the V3 costed finite-domain search under an explicit synthetic baseline."""
import argparse
import json
from pathlib import Path
from src.daily_planning.integrated_v3.optimizer import optimize_72h
from src.daily_planning.physical_scheduling.user_policy import build_unbounded_synthetic_policy


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('snapshot',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--time-limit',type=float,default=60);p.add_argument('--candidate-policy',default='V3_ETB_EVENTS_V1');p.add_argument('--inventory-relaxation',action='store_true');p.add_argument('--reuse-physical-model',action='store_true');args=p.parse_args()
    if args.output.exists():raise ValueError('Output exists; use a new path')
    s=json.loads(args.snapshot.read_text());start=s['fixed_process_flows']['inventory_as_of_at']
    policy=build_unbounded_synthetic_policy(s,start_at=start,end_at=s['fixed_process_flows']['flow_horizon_end_at']);policy['cargo_duration_policy']='VESSEL_BERTH_RATE_V1'
    result=optimize_72h(s,as_of_at=start,policy=policy,settlement_records=[],pricing_policy='CLEAR_WEATHER_BASELINE_V1',inventory_cuts=True,time_limit_seconds=args.time_limit,candidate_event_policy=args.candidate_policy,inventory_relaxation=args.inventory_relaxation,reuse_physical_model=args.reuse_physical_model)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    incumbent=result.get('incumbent') or {};costs=[r['demurrage_cents'] for r in result.get('iterations',[]) if r.get('demurrage_cents') is not None]
    print(json.dumps({'status':result['status'],'elapsed_seconds':result['elapsed_seconds'],'iterations':len(result['iterations']),'first_cost_cents':costs[0] if costs else None,'best_cost_cents':incumbent.get('total_demurrage_cents'),'unresolved_candidate_count':len(result['unresolved_candidates']),'domain_exhausted':result['domain_exhausted'],'output':str(args.output),'operational_approval':False},ensure_ascii=False))
    return 0 if incumbent else 2
if __name__=='__main__':raise SystemExit(main())
