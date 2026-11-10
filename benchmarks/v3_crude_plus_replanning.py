"""Synthetic, read-only next-day candidate trial using prior projected state."""
import argparse
import json
from pathlib import Path
from src.daily_planning.integrated_v3.search import search_with_events
from src.daily_planning.physical_scheduling.user_policy import build_unbounded_synthetic_policy


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('snapshot',type=Path);p.add_argument('continuity',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--time-limit',type=float,default=30)
    args=p.parse_args();snapshot=json.loads(args.snapshot.read_text());prior=json.loads(args.continuity.read_text())
    if prior.get('operational_approval') is not False or prior.get('reconciliation',{}).get('status')!='RECONCILED':raise ValueError('Validated synthetic continuity evidence required')
    trial=prior['trial'];tank=trial['validation']['tank_result'];boundary=tank['carry_at'];active={row['call_id'] for row in tank['carried_transfers']}
    rows=trial['berth_result']['schedule'];locked=[row for row in rows if row['call_id'] in active];defs=[row for row in tank['transfer_definitions'] if row['call_id'] in active]
    if len(locked)!=len(active) or len(defs)!=len(active):raise ValueError('Missing protected in-progress assignment')
    policy=build_unbounded_synthetic_policy(snapshot,start_at=snapshot['fixed_process_flows']['inventory_as_of_at'],end_at='2026-11-11T00:00:00+09:00');policy['cargo_duration_policy']='VESSEL_BERTH_RATE_V1'
    result=search_with_events(snapshot,as_of_at=boundary,policy=policy,commitments=locked,previous_schedule=rows,initial_inventory=tank['carried_inventory_bbl'],carried_transfers=tank['carried_transfers'],tank_commitments=defs,inventory_cuts=True,time_limit_seconds=args.time_limit,max_iterations=5)
    output={'scenario':'V3_CRUDE_PLUS_3','scope':'SYNTHETIC_REPLANNING_USING_PRIOR_PROJECTIONS_NOT_OBSERVATIONS','boundary':boundary,'protected_calls':sorted(active),'result':result,'operational_approval':False}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as out:json.dump(output,out,ensure_ascii=False,indent=2)
    print(json.dumps({'status':result['status'],'protected_calls':sorted(active),'iterations':result.get('iterations',[]),'issues':result.get('issues',[]),'output':str(args.output),'operational_approval':False},ensure_ascii=False))
    return 0 if result['status']=='PHYSICAL_AND_TANK_ONLY' else 2

if __name__=='__main__':raise SystemExit(main())
