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
                       same_tank_policy=None, time_limit_seconds=30, inventory_cuts=False, protected_call_ids=(), inventory_relaxation=False, capacity_bound_policy='DISABLED', projection_end_at=None):
    """Validate a supplied berth candidate and propose tank allocation.

    Input rows must be the existing physical scheduler's full replay format.
    This interface does not optimize ETB, price demurrage, or approve operation.
    """
    if snapshot.get('scenario_id')=='V5_3TANK':
        return validate_v5_candidate(snapshot,rows,as_of_at=as_of_at,projection_end_at=projection_end_at,
            initial_inventory=initial_inventory,carried_transfers=carried_transfers,
            tank_commitments=tank_commitments,protected_call_ids=protected_call_ids,same_tank_policy='ALLOW' if same_tank_policy is None else same_tank_policy)
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
        protected=set(protected_call_ids)
        if not protected<={row['call_id'] for row in rows}:raise ValueError('Protected call absent from physical rows')
        for row in rows:
            if timestamp(row['work_end_at'])<=begin:
                if row['call_id'] not in protected:raise ValueError('Completed transfer requires an explicit protected physical row')
                continue
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
        tank=solve_v3_allocation(snapshot,transfer_inputs,start_at=begin.isoformat(),end_at=finish.isoformat(),initial_inventory=initial_inventory,carried_transfers=carried_transfers,commitments=tank_commitments,same_tank_policy=same_tank_policy,time_limit_seconds=time_limit_seconds,inventory_cuts=inventory_cuts,inventory_relaxation=inventory_relaxation,**({'capacity_bound_policy':capacity_bound_policy} if capacity_bound_policy!='DISABLED' else {}))
        if tank['status']!='VALID':
            return {'model_version':VERSION,'status':'TANK_SUBPROBLEM_NOT_VALIDATED','physical_validation':physical,'tank_result':tank,'operational_approval':False}
        return {'model_version':VERSION,'status':'PHYSICAL_AND_TANK_ONLY','source_fingerprint':digest(canonical(snapshot).encode()),'policy_fingerprint':digest(canonical(policy).encode()),'physical_validation':physical,'tank_result':tank,'pending_checks':['CONTRACT_SETTLEMENT','EXTERNAL_WEATHER','OBJECTIVE_AND_CANDIDATE_SEARCH','FULL_COMMITMENT_AND_FREEZE_VALIDATION','COMPLETE_SCENARIO_AND_ROLLING_COVERAGE'],'operational_approval':False}
    except (ValueError,KeyError,TypeError,ZeroDivisionError,OverflowError) as exc:
        return failure('INVALID_INPUT','INTEGRATION_INPUT_INVALID',exc)


def validate_v5_candidate(snapshot, rows, *, as_of_at, projection_end_at=None,
                          initial_inventory=None, carried_transfers=None,
                          tank_commitments=None, protected_call_ids=(), same_tank_policy='ALLOW'):
    """Convert candidate work intervals to exact transfer segments; no berth approval."""
    from ..tank.v5_inventory import validate_v5_inventory
    try:
        begin=timestamp(as_of_at)
        end=timestamp(projection_end_at) if projection_end_at else begin+timedelta(hours=72)
        calls={c['call_id']:c for c in snapshot['calls']};berths={b['berth_id']:b for b in snapshot['berths']}
        protected=set(protected_call_ids); supplied={r.get('call_id') for r in rows}
        committed={r.get('call_id') for r in (tank_commitments or [])}
        if not protected<=supplied or not protected<=committed:
            return {'model_version':'V5_INVENTORY_CHECKPOINT_V1','status':'TANK_SUBPROBLEM_NOT_VALIDATED','issues':[{'code':'INVALID_CARRYOVER_STATE','reason':'Protected calls require candidate rows and unchanged tank commitments','call_ids':sorted(protected-(supplied&committed))}],'physical_validation':'NOT_EVALUATED','scheduling_feasibility':'NOT_EVALUATED','operational_approval':False}
        transfers=[]
        for row in rows:
            c=calls[row['call_id']];b=berths[row['berth_id']]
            ratio=number(c['physical']['bbl_per_mt']);remaining=number(c['cargo_volume_bbl'])
            rates=[number(v) for v in (c.get('transfer_rate_mt_h'),b.get('max_transfer_rate_mt_h')) if v not in (None,'')]
            if not rates or min(rates)<=0 or ratio<=0:raise ValueError('Invalid transfer rate: '+row['call_id'])
            rate=min(rates)*ratio;pieces=[]
            for first,last in row['work_intervals']:
                a,z=timestamp(first),timestamp(last);seconds=int((z-a).total_seconds())
                if seconds<=0:raise ValueError('Invalid work interval: '+row['call_id'])
                full=min(seconds,int(remaining*3600//rate))
                if full:
                    q=a+timedelta(seconds=full);pieces.append({'start_at':a.isoformat(),'end_at':q.isoformat(),'rate_bbl_per_hour':str(rate)});remaining-=rate*Fraction(full,3600);a=q;seconds-=full
                if remaining and seconds:
                    partial=remaining*3600
                    if partial>rate:raise ValueError('Insufficient transfer time: '+row['call_id'])
                    q=a+timedelta(seconds=1);pieces.append({'start_at':a.isoformat(),'end_at':q.isoformat(),'rate_bbl_per_hour':str(partial)});remaining=Fraction(0);a=q;seconds-=1
                if not remaining and seconds:raise ValueError('Excess transfer time: '+row['call_id'])
            if remaining or not pieces:raise ValueError('Incomplete transfer: '+row['call_id'])
            transfer={'call_id':row['call_id'],'berth_id':row['berth_id'],'start_at':pieces[0]['start_at'],'end_at':pieces[-1]['end_at'],'segments':pieces,'etb_at':row.get('etb_at')}
            transfers.append(transfer)
        tank=validate_v5_inventory(snapshot,transfers,start_at=begin.isoformat(),end_at=end.isoformat(),initial_inventory=initial_inventory,carried_transfers=carried_transfers,commitments=tank_commitments,same_tank_policy=same_tank_policy)
        return {'model_version':'V5_INVENTORY_CHECKPOINT_V1','status':'INVENTORY_VALID' if tank['status']=='VALID' else 'TANK_SUBPROBLEM_NOT_VALIDATED','tank_result':tank,'issues':tank['issues'],'physical_validation':'NOT_EVALUATED','scheduling_feasibility':'NOT_EVALUATED','operational_approval':False}
    except (KeyError,ValueError,TypeError,ZeroDivisionError,OverflowError) as exc:
        return {'model_version':'V5_INVENTORY_CHECKPOINT_V1','status':'TANK_SUBPROBLEM_NOT_VALIDATED','issues':[{'code':'INVALID_TRANSFER_QUANTITY','reason':str(exc)}],'physical_validation':'NOT_EVALUATED','scheduling_feasibility':'NOT_EVALUATED','operational_approval':False}
