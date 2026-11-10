"""Conservative validation bridge for existing V3 berth and tank subproblems.

This is an integration checkpoint: it does not price contracts or search for
an alternative berth proposal when tank allocation fails.
"""
from datetime import timedelta
from fractions import Fraction
from ..integrated_input.validator import number, timestamp, tokens
from ..integrated_input.provenance import canonical, digest
from ..physical_scheduling.validator import validate_result
from ..tank.v3_optimization import solve_v3_allocation

VERSION='V3_INTEGRATION_CHECKPOINT_V1'


def validate_candidate(snapshot, rows, *, policy, as_of_at, initial_inventory=None,
                       carried_transfers=None, tank_commitments=None,
                       same_tank_policy=None, time_limit_seconds=30, inventory_cuts=False):
    """Validate a supplied berth candidate and propose tank allocation.

    Input rows must be the existing physical scheduler's full replay format.
    This interface does not optimize ETB, price demurrage, or approve operation.
    """
    def failure(status,code,reason):
        return {'model_version':VERSION,'status':status,'issues':[{'code':code,'reason':str(reason)}], 'operational_approval':False}
    try:
        begin=timestamp(as_of_at)
        if not rows:raise ValueError('At least one physical row is required')
        physical=validate_result(snapshot,rows,policy)
        if physical['status']!='VALID':
            return {'model_version':VERSION,'status':'PHYSICAL_VALIDATION_FAILED','issues':physical['issues'],'operational_approval':False}
        calls={c['call_id']:c for c in snapshot['calls']};berths={b['berth_id']:b for b in snapshot['berths']};tanks={t['tank_id']:t for t in snapshot['tanks']}
        transfer_inputs=[];finish=begin+timedelta(hours=72)
        commitments={x['call_id']:x for x in (tank_commitments or [])}
        if len(commitments)!=len(tank_commitments or []):raise ValueError('Duplicate tank commitment')
        for row in rows:
            c=calls[row['call_id']];b=berths[row['berth_id']];vol=number(c['cargo_volume_bbl']);ratio=number(c['physical']['bbl_per_mt'])
            rates=[number(v) for v in (c.get('transfer_rate_mt_h'),b.get('max_transfer_rate_mt_h')) if v not in (None,'')]
            if not rates or min(rates)<=0 or ratio<=0:raise ValueError('Missing valid vessel/berth rate for '+row['call_id'])
            rate=min(rates)*ratio;remaining=vol;segments=[]
            for interval in row['work_intervals']:
                a,z=map(timestamp,interval);seconds=int((z-a).total_seconds())
                if seconds<=0:raise ValueError('Invalid work interval')
                full=min(seconds,int(remaining*3600//rate))
                if full:
                    segment_end=a+timedelta(seconds=full);segments.append({'start_at':a.isoformat(),'end_at':segment_end.isoformat(),'rate_bbl_per_hour':str(rate)})
                    remaining-=rate*Fraction(full,3600);a=segment_end;seconds-=full
                if remaining and seconds:
                    part=remaining*3600
                    if part>rate:raise ValueError('Transfer rate cannot cover workload')
                    segment_end=a+timedelta(seconds=1);segments.append({'start_at':a.isoformat(),'end_at':segment_end.isoformat(),'rate_bbl_per_hour':str(part)});remaining=Fraction(0);a=segment_end;seconds-=1
                if not remaining and (seconds or a<z):raise ValueError('Physical work duration exceeds fixed transfer assumptions')
            if remaining or not segments:raise ValueError('Physical work intervals do not preserve cargo at effective rate')
            if timestamp(segments[-1]['end_at'])!=timestamp(row['work_end_at']):raise ValueError('Cargo completion differs from berth phase replay')
            finish=max(finish,timestamp(row['work_end_at']))
            options=[tid for tid in tokens(b['connected_tank_ids']) if tid in tanks and row['berth_id'] in tokens(tanks[tid]['connected_berth_ids']) and tanks[tid]['cargo_group']==c['cargo_group']]
            if not options:raise ValueError('No verified compatible tank for '+row['call_id'])
            transfer={'call_id':row['call_id'],'berth_id':row['berth_id'],'start_at':segments[0]['start_at'],'end_at':segments[-1]['end_at'],'segments':segments,'etb_at':row['etb_at']}
            if row['call_id'] in commitments:
                committed=commitments[row['call_id']]
                if any(committed.get(k)!=v for k,v in transfer.items()):raise ValueError('Protected tank transfer differs from phase replay')
                transfer=committed
            else:transfer['eligible_tank_ids']=options
            transfer_inputs.append(transfer)
        tank=solve_v3_allocation(snapshot,transfer_inputs,start_at=begin.isoformat(),end_at=finish.isoformat(),initial_inventory=initial_inventory,carried_transfers=carried_transfers,commitments=tank_commitments,same_tank_policy=same_tank_policy,time_limit_seconds=time_limit_seconds,inventory_cuts=inventory_cuts)
        if tank['status']!='VALID':
            return {'model_version':VERSION,'status':'TANK_SUBPROBLEM_NOT_VALIDATED','physical_validation':physical,'tank_result':tank,'operational_approval':False}
        return {'model_version':VERSION,'status':'PHYSICAL_AND_TANK_ONLY','source_fingerprint':digest(canonical(snapshot).encode()),'policy_fingerprint':digest(canonical(policy).encode()),'physical_validation':physical,'tank_result':tank,'pending_checks':['CONTRACT_SETTLEMENT','EXTERNAL_WEATHER','OBJECTIVE_AND_CANDIDATE_SEARCH','FULL_COMMITMENT_AND_FREEZE_VALIDATION','COMPLETE_SCENARIO_AND_ROLLING_COVERAGE'],'operational_approval':False}
    except (ValueError,KeyError,TypeError,ZeroDivisionError,OverflowError) as exc:
        return failure('INVALID_INPUT','INTEGRATION_INPUT_INVALID',exc)
