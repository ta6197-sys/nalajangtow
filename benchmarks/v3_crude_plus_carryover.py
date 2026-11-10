"""Read-only 24h continuity check of a synthetic V3 crude-plus projection."""
import argparse
import json
from pathlib import Path
from src.daily_planning.integrated_v3.search import search_with_events
from src.daily_planning.integrated_v3.carryover import reconcile_projection
from src.daily_planning.physical_scheduling.user_policy import build_unbounded_synthetic_policy


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshot',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--time-limit',type=float,default=30)
    a=p.parse_args();s=json.loads(a.snapshot.read_text());begin=s['fixed_process_flows']['inventory_as_of_at']
    policy=build_unbounded_synthetic_policy(s,start_at=begin,end_at='2026-11-11T00:00:00+09:00');policy['cargo_duration_policy']='VESSEL_BERTH_RATE_V1'
    trial=search_with_events(s,as_of_at=begin,policy=policy,time_limit_seconds=a.time_limit,inventory_cuts=True,max_iterations=5)
    outcome={'scenario':'V3_CRUDE_PLUS_3','trial':trial,'reconciliation':None,'operational_approval':False}
    if trial['status']=='PHYSICAL_AND_TANK_ONLY':
        outcome['reconciliation']=reconcile_projection(s,trial['validation']['tank_result'])
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as out:json.dump(outcome,out,ensure_ascii=False,indent=2)
    r=outcome['reconciliation'] or {}
    print(json.dumps({'trial_status':trial['status'],'reconciliation_status':r.get('status'),'tank_count':r.get('tank_count'),'in_progress_calls':r.get('in_progress_calls'),'mismatch_count':len(r.get('mismatches',[])),'output':str(a.output),'operational_approval':False},ensure_ascii=False))
    return 0 if r.get('status')=='RECONCILED' else 2

if __name__=='__main__':raise SystemExit(main())
