"""Synthetic rolling-horizon validation; never updates operational state."""
import argparse
import json
from pathlib import Path
from src.daily_planning.integrated_v3.synthetic_rolling import run_synthetic_trial
from src.daily_planning.physical_scheduling.user_policy import build_unbounded_synthetic_policy


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('snapshot',type=Path);parser.add_argument('--output',required=True,type=Path);parser.add_argument('--days',type=int,default=7);parser.add_argument('--seconds-per-day',type=float,default=30);parser.add_argument('--flow-anchors',action='store_true')
    args=parser.parse_args();snapshot=json.loads(args.snapshot.read_text());start=snapshot['fixed_process_flows']['inventory_as_of_at']
    policy=build_unbounded_synthetic_policy(snapshot,start_at=start,end_at=snapshot['fixed_process_flows']['flow_horizon_end_at']);policy['cargo_duration_policy']='VESSEL_BERTH_RATE_V1'
    result=run_synthetic_trial(snapshot,as_of_at=start,policy=policy,days=args.days,time_limit_per_day=args.seconds_per_day,candidate_event_policy='V3_ETB_FLOW_EVENTS_V2' if args.flow_anchors else 'V3_ETB_EVENTS_V1')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as out:json.dump(result,out,ensure_ascii=False,indent=2)
    print(json.dumps({'status':result['status'],'steps':[step['summary'] for step in result['steps']],'output':str(args.output),'operational_approval':False},ensure_ascii=False))
    return 0 if result['status']=='ALL_REQUESTED_DAYS_SUBSYSTEM_VALID' else 2

if __name__=='__main__':raise SystemExit(main())
