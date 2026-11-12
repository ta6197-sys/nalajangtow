"""Second-precision phase replay for one explicit ETB/berth proposal."""
from datetime import timedelta
from ..integrated_input.validator import timestamp,number
from .compatibility import compatibility,berth_duration_seconds,vessel_berth_duration_seconds
from .v5_user_rules import enabled as user_v5, VERSION as USER_V5, HOSE_DISCONNECT_SECONDS, no_night_etb


def seconds_from_minutes(value):
    result=number(value)*60
    if result.denominator!=1 or result<0:raise ValueError('Duration must resolve to nonnegative integer seconds')
    return int(result)


def covered(a,b,windows):
    return any(timestamp(w['start_at'])<=a and b<=timestamp(w['end_at']) and (not w.get('latest_start_at') or a<=timestamp(w['latest_start_at'])) for w in windows)


def replay(call,berth,etb,policy,*,restrictions=(),hose_connection_start_at=None,unberthing_start_at=None):
    if compatibility(call,berth):raise ValueError('Incompatible call/berth')
    config=policy['berths'][berth['berth_id']]
    new_phase=user_v5(policy)
    by_call_tugs=config.get('tug_units_by_call')
    if by_call_tugs is not None and call['call_id'] not in by_call_tugs:raise ValueError('Call-specific tug requirement missing')
    common_tugs=by_call_tugs[call['call_id']] if by_call_tugs is not None else config.get('tug_units')
    etb=timestamp(etb);eta=timestamp(call['eta_at'])
    if new_phase and no_night_etb(call,etb):raise ValueError('V5 user-policy 02:00–08:00 ETB restriction')
    if call['fixed_etb_is_hard'] and etb!=timestamp(call['fix_etb_at']):raise ValueError('FIX ETB cannot move')
    berthing_start=etb-timedelta(seconds=config['berthing_seconds'])
    pob=berthing_start-timedelta(seconds=config.get('pilot_boarding_to_berthing_seconds',0))
    if pob<eta:raise ValueError('POB before ETA')
    call_windows=config.get('pilotage_windows_by_call',{}).get(call['call_id'])
    if config.get('pilotage_windows_by_call') is not None and call_windows is None:raise ValueError('Call-specific pilotage windows missing')
    berthing_windows=call_windows['berthing'] if call_windows else config.get('pilotage_windows')
    unberthing_windows=(None if new_phase else (call_windows['unberthing'] if call_windows else config.get('pilotage_windows')))
    if berthing_windows is not None and not covered(pob,etb,berthing_windows):raise ValueError('Berthing outside pilotage window')
    hose_start=etb if hose_connection_start_at is None else timestamp(hose_connection_start_at)
    if hose_start<etb:raise ValueError('Hose connection cannot begin before ETB')
    ready=hose_start+timedelta(seconds=config['hose_connection_seconds'])
    rate_policy=policy.get('cargo_duration_policy','BERTH_RATE_V1')
    duration=(vessel_berth_duration_seconds(call,berth) if rate_policy=='VESSEL_BERTH_RATE_V1' else berth_duration_seconds(call,berth))
    left=duration;work=[]
    windows=[(timestamp(w['start_at']),timestamp(w['end_at'])) for w in config['work_windows']]
    for block in restrictions:
        if block['berth_id']!=berth['berth_id'] or block.get('cargo_group') not in (None,'',call['cargo_group']):continue
        if block['restriction_scope'] not in ('WORK','BOTH'):raise ValueError('Unsupported restriction scope')
        lo,hi=timestamp(block['start_local']),timestamp(block['end_local'])
        if hi<=lo:raise ValueError('Invalid restriction interval')
        trimmed=[]
        for a,b in windows:
            if hi<=a or lo>=b:trimmed.append((a,b))
            else:
                if a<lo:trimmed.append((a,lo))
                if hi<b:trimmed.append((hi,b))
        windows=trimmed
    for left_bound,right_bound in sorted(windows):
        a=max(ready,left_bound);b=right_bound
        if b<=a:continue
        used=min(left,int((b-a).total_seconds()))
        if used:
            work.append((a,a+timedelta(seconds=used)));left-=used;ready=work[-1][1]
        if not left:break
    if left:raise ValueError('Insufficient permitted cargo-work window coverage')
    work_start,work_end=work[0][0],work[-1][1]
    hose_end=work_end+timedelta(seconds=HOSE_DISCONNECT_SECONDS if new_phase else seconds_from_minutes(berth['hose_disconnect_min']))
    if new_phase:
        if unberthing_start_at is not None and timestamp(unberthing_start_at)!=hose_end:
            raise ValueError('V5 ETU is fixed at the 120-minute hose-disconnection end')
        unberthing_start=etu=departure_pob=hose_end
        # Compatibility placeholders are zero-length; they are NOT switching
        # operations. Conditional inter-vessel release is checked by pair_errors.
        switch_start=switch_end=etu
    else:
        unberthing_start=hose_end if unberthing_start_at is None else timestamp(unberthing_start_at)
        if unberthing_start<hose_end:raise ValueError('Unberthing before hose disconnection completion')
        etu=unberthing_start+timedelta(seconds=config['unberthing_seconds'])
        departure_pob=unberthing_start-timedelta(seconds=config.get('pilot_boarding_to_unberthing_seconds',0))
        if unberthing_windows is not None and not covered(departure_pob,etu,unberthing_windows):raise ValueError('Unberthing outside pilotage window')
        switch_start=hose_end
        if berth['berth_id'] in policy.get('dock_4142_berths',[]) and policy['dock_4142_version']=='SYNTHETIC_WORK_END_V1':switch_start=work_end
        switch_end=switch_start+timedelta(seconds=seconds_from_minutes(berth['switching_min']))
    row={'call_id':call['call_id'],'berth_id':berth['berth_id'],'cargo_group':call['cargo_group'],
        'pob_at':pob.isoformat(),'pilot_boarding_at':pob.isoformat(),'berthing_start_at':berthing_start.isoformat(),
        'etb_at':etb.isoformat(),'hose_connection_start_at':hose_start.isoformat(),
        'hose_connection_end_at':(hose_start+timedelta(seconds=config['hose_connection_seconds'])).isoformat(),
        'departure_pob_at':departure_pob.isoformat(),'unberthing_start_at':unberthing_start.isoformat(),
        'work_start_at':work_start.isoformat(),'work_end_at':work_end.isoformat(),'hose_disconnect_end_at':hose_end.isoformat(),
        'etu_at':etu.isoformat(),'physical_clearance_at':etu.isoformat(),'switching_start_at':switch_start.isoformat(),'switching_end_at':switch_end.isoformat(),
        'work_intervals':[[a.isoformat(),b.isoformat()] for a,b in work],
        'work_seconds':duration,'duration_basis':('VESSEL_BERTH_RATE_TANK_NOT_VALIDATED' if rate_policy=='VESSEL_BERTH_RATE_V1' else 'BERTH_RATE_LOWER_BOUND_TANK_NOT_VALIDATED'),
        'tug_units':{'berthing':config.get('berthing_tug_units',common_tugs),'unberthing':config.get('unberthing_tug_units',common_tugs)}}
    if new_phase:
        row.update(phase_policy_version=USER_V5, direction_seconds=config['direction_seconds'],
                   switching_rule_seconds=config['switching_seconds'])
    return row
