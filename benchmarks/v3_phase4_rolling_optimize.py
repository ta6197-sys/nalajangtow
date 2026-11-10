"""Run the approved clear-weather synthetic costed rolling experiment."""
import argparse,json
from pathlib import Path
from src.daily_planning.integrated_v3.rolling_optimizer import run_costed_rolling
from src.daily_planning.physical_scheduling.user_policy import build_unbounded_synthetic_policy

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('snapshot',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--days',type=int,default=58);p.add_argument('--seconds-per-policy',type=float,default=15);a=p.parse_args()
    if a.output.exists():raise ValueError('Output exists; choose a new path')
    s=json.loads(a.snapshot.read_text());begin=s['fixed_process_flows']['inventory_as_of_at'];policy=build_unbounded_synthetic_policy(s,start_at=begin,end_at=s['fixed_process_flows']['flow_horizon_end_at']);policy['cargo_duration_policy']='VESSEL_BERTH_RATE_V1'
    r=run_costed_rolling(s,as_of_at=begin,policy=policy,days=a.days,time_limit_per_day=a.seconds_per_policy)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f:json.dump(r,f,ensure_ascii=False,indent=2)
    print(json.dumps({'status':r['status'],'days_requested':a.days,'days_attempted':len(r['steps']),'steps':[{'day':v['day'],'as_of_at':v['as_of_at'],'trials':v['trials'],'cost_cents':(v['result'].get('incumbent') or {}).get('total_demurrage_cents')} for v in r['steps']],'output':str(a.output),'operational_approval':False},ensure_ascii=False))
    return 0 if r['status']=='REQUESTED_WINDOWS_VALIDATED' else 2
if __name__=='__main__':raise SystemExit(main())
