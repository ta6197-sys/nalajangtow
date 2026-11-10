"""Compare finite event policies using the same prior synthetic rolling state."""
import argparse
import json
import time
from pathlib import Path
from src.daily_planning.integrated_v3.search import search_with_events
from src.daily_planning.physical_scheduling.user_policy import build_unbounded_synthetic_policy


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('snapshot',type=Path);p.add_argument('rolling',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--seconds',type=float,default=30);p.add_argument('--policy',choices=('V3_ETB_FLOW_EVENTS_V2','V3_ETB_TENTATIVE_RELEASES_V3'),required=True)
    a=p.parse_args()
    if a.output.exists():raise ValueError('Output exists; use a new path')
    s=json.loads(a.snapshot.read_text());rolling=json.loads(a.rolling.read_text());state=rolling['last_simulated_state']
    if state.get('state_origin')!='SIMULATED_PROJECTION_NOT_OBSERVATION':raise ValueError('Synthetic prior projection state required')
    policy=build_unbounded_synthetic_policy(s,start_at=s['fixed_process_flows']['inventory_as_of_at'],end_at=s['fixed_process_flows']['flow_horizon_end_at']);policy['cargo_duration_policy']='VESSEL_BERTH_RATE_V1'
    values={key:state[key] for key in ('completed_rows','commitments','tank_commitments','carried_transfers','initial_inventory','previous_schedule')}
    started=time.monotonic();result=search_with_events(s,as_of_at=state['boundary'],policy=policy,inventory_cuts=True,time_limit_seconds=a.seconds,max_iterations=5,candidate_event_policy=a.policy,**values);elapsed=time.monotonic()-started
    evidence={'scope':'SAME_SYNTHETIC_ROLLING_STATE_NOT_OPERATIONAL','elapsed_seconds':elapsed,'policy':a.policy,'result':result,'operational_approval':False}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f:json.dump(evidence,f,ensure_ascii=False,indent=2)
    print(json.dumps({'status':result['status'],'seconds':elapsed,'candidate_times':sum(map(len,result.get('candidate_generation',{}).get('candidate_etbs',{}).values())),'berth_issues':result.get('berth_result',{}).get('issues',[]),'output':str(a.output)},ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
