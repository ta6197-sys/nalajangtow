"""Explicit, versioned synthetic phase policy; no operational defaults."""
from ..integrated_input.validator import timestamp

VERSION='V3_SYNTHETIC_PHASE_POLICY_V1'


def validate_policy(policy,berth_ids):
    if not isinstance(policy,dict) or policy.get('schema_version')!=VERSION:
        raise ValueError('Explicit versioned phase policy is required')
    if policy.get('data_origin')!='SYNTHETIC_FIXTURE' or policy.get('operational_approval') is not False:
        raise ValueError('This policy version is synthetic and never operationally approved')
    if not policy.get('source_reference'):raise ValueError('Policy source reference is required')
    for bid in berth_ids:
        row=policy.get('berths',{}).get(bid)
        if row is None:raise ValueError('Missing phase policy for '+bid)
        for key in ('berthing_seconds','hose_connection_seconds','unberthing_seconds'):
            if type(row.get(key)) is not int or row[key]<0:raise ValueError('Invalid phase parameter: '+bid+'.'+key)
        if row.get('tug_units_by_call') is not None:
            mapping=row['tug_units_by_call']
            if not isinstance(mapping,dict) or not mapping or any(type(value) is not int or value<0 for value in mapping.values()):raise ValueError('Invalid call-specific tug demand')
        else:
            for key in ('berthing_tug_units','unberthing_tug_units'):
                value=row.get(key,row.get('tug_units'))
                if type(value) is not int or value<0:raise ValueError('Explicit tug demand required: '+bid+'.'+key)
        for key in ('pilot_boarding_to_berthing_seconds','pilot_boarding_to_unberthing_seconds'):
            if type(row.get(key,0)) is not int or row.get(key,0)<0:raise ValueError('Invalid pilot boarding transit')
        window_groups={'work_windows':row.get('work_windows')}
        by_call=row.get('pilotage_windows_by_call')
        if by_call is None:window_groups['pilotage_windows']=row.get('pilotage_windows')
        elif not isinstance(by_call,dict) or not by_call:raise ValueError('Missing call-specific pilotage map')
        else:
            for cid,parts in by_call.items():
                if not isinstance(parts,dict):raise ValueError('Invalid call pilotage windows')
                for phase in ('berthing','unberthing'):window_groups[cid+'.'+phase]=parts.get(phase)
        for key,windows in window_groups.items():
            if not isinstance(windows,list) or not windows:raise ValueError('Missing '+bid+'.'+key)
            parsed=[]
            for window in windows:
                a,b=timestamp(window['start_at']),timestamp(window['end_at'])
                if b<=a:raise ValueError('Invalid policy window')
                if window.get('latest_start_at') and not a<=timestamp(window['latest_start_at'])<=b:raise ValueError('Invalid Last POB bound')
                parsed.append((a,b))
            ordered=sorted(parsed)
            if any(a[1]>b[0] for a,b in zip(ordered,ordered[1:])):raise ValueError('Overlapping policy windows')
    if type(policy.get('tug_capacity')) is not int or policy['tug_capacity']<0:raise ValueError('Explicit tug capacity required')
    if policy.get('dock_4142_version') not in ('DISABLED','SYNTHETIC_WORK_END_V1','SYNTHETIC_HOSE_END_V1'):
        raise ValueError('Explicit 4142 switching interpretation required')
    if policy['dock_4142_version']!='DISABLED' and (not isinstance(policy.get('dock_4142_berths'),list) or len(set(policy['dock_4142_berths']))!=2 or not set(policy['dock_4142_berths'])<=set(berth_ids)):
        raise ValueError('4142 needs two explicit existing berth IDs')
    if policy.get('dock_4142_berthing_resource','MUTEX') not in ('MUTEX','INDEPENDENT'):raise ValueError('Invalid berth maneuver resource policy')
    if policy.get('legacy_etu_after_work_seconds') is not None and (type(policy['legacy_etu_after_work_seconds']) is not int or policy['legacy_etu_after_work_seconds']<0):raise ValueError('Invalid legacy ETU reference')
    if policy['dock_4142_version']!='DISABLED':
        resources=policy.get('dock_4142_shared_resources')
        allowed={'BERTHING','HOSE_CONNECT','CARGO','HOSE_DISCONNECT','SWITCHING','UNBERTHING'}
        if not isinstance(resources,dict) or not resources or not any('CARGO' in phases for phases in resources.values()):raise ValueError('Explicit 4142 shared phase resources required')
        if any(not isinstance(phases,list) or not phases or len(phases)!=len(set(phases)) or not set(phases)<=allowed for phases in resources.values()):raise ValueError('Invalid shared resource phase map')
    return policy


def event_definition_issues(snapshot,policy):
    """Record the disputed ETU relation, never reinterpret it silently."""
    if not any(g.get('rule_type')=='PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP' for g in snapshot['workbook_tables']['groups']['rows']):return []
    from .phases import seconds_from_minutes
    reference=policy.get('legacy_etu_after_work_seconds')
    issues=[]
    if reference is None:
        return [{'code':'MISSING_EVENT_DEFINITION','field':'legacy_etu_after_work_seconds','reason':'Explicit status of legacy work-end to ETU reference is required'}]
    for berth in snapshot['berths']:
        bid=berth['berth_id']
        if bid not in policy.get('dock_4142_berths',[]):continue
        actual=seconds_from_minutes(berth['hose_disconnect_min'])+policy['berths'][bid]['unberthing_seconds']
        if actual!=reference:
            issues.append({'code':'EVENT_DEFINITION_CONFLICT','berth_id':bid,'legacy_work_end_to_etu_seconds':reference,
                           'phased_work_end_to_etu_seconds':actual,
                           'reason':'Hose disconnection plus separate unberthing differs from the legacy ETU relation; explicit policy resolution required'})
    return issues
