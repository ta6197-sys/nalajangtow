"""Read-only, source-backed partial tank trials; not a physical schedule."""
import argparse
import json
from datetime import timedelta
from fractions import Fraction
from pathlib import Path

from src.daily_planning.integrated_input.validator import number, timestamp, tokens
from src.daily_planning.physical_scheduling.compatibility import compatibility
from src.daily_planning.tank.v3_inventory import validate_v3_inventory


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot',type=Path)
    parser.add_argument('--count',type=int,default=3)
    args=parser.parse_args()
    if args.count<1:parser.error('--count must be positive')
    snapshot=json.loads(args.snapshot.read_text());flow=snapshot['fixed_process_flows']
    start=timestamp(flow['inventory_as_of_at']);end=start+timedelta(hours=96);results=[]
    for call in sorted(snapshot['calls'],key=lambda row:(timestamp(row['eta_at']),row['call_id'])):
        if call['fixed_etb_is_hard'] or timestamp(call['eta_at'])>=start+timedelta(hours=72):continue
        found=False
        for berth in snapshot['berths']:
            if compatibility(call,berth):continue
            candidates=[tank for tank in snapshot['tanks'] if tank['tank_id'] in tokens(berth['connected_tank_ids']) and berth['berth_id'] in tokens(tank['connected_berth_ids']) and tank['cargo_group']==call['cargo_group']]
            if not candidates:continue
            rate=number(berth['max_transfer_rate_mt_h'])*number(call['physical']['bbl_per_mt']);volume=number(call['cargo_volume_bbl'])
            duration=volume*3600/rate;full_seconds=duration.numerator//duration.denominator;remaining=volume-rate*Fraction(full_seconds,3600)
            begin=max(start,timestamp(call['eta_at']))+timedelta(hours=4);split=begin+timedelta(seconds=full_seconds);segments=[]
            if full_seconds:segments.append({'start_at':begin.isoformat(),'end_at':split.isoformat(),'rate_bbl_per_hour':str(rate)})
            if remaining:segments.append({'start_at':split.isoformat(),'end_at':(split+timedelta(seconds=1)).isoformat(),'rate_bbl_per_hour':str(remaining*3600)})
            finish=timestamp(segments[-1]['end_at'])
            if finish>end:continue
            transfer={'call_id':call['call_id'],'berth_id':berth['berth_id'],'tank_id':candidates[0]['tank_id'],'start_at':begin.isoformat(),'end_at':finish.isoformat(),'segments':segments}
            result=validate_v3_inventory(snapshot,[transfer],start_at=start.isoformat(),end_at=end.isoformat())
            results.append({'transfer':transfer,'source_quantity_bbl':str(volume),'status':result['status'],'issue_count':len(result['issues']),'first_issues':result['issues'][:3]})
            found=True;break
        if found and len(results)>=args.count:break
    print(json.dumps({'scope':'ILLUSTRATIVE_INDEPENDENT_SINGLE_CALL_TRIALS_NOT_SCHEDULES','operational_approval':False,'trial_count':len(results),'results':results},indent=2,ensure_ascii=False))

if __name__=='__main__':main()
