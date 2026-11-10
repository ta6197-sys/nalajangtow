"""User-approved synthetic defaults; unresolved berth rules remain explicit."""
import copy
import csv
from datetime import timedelta
from pathlib import Path
from ..integrated_input.provenance import digest
from ..integrated_input.validator import number,timestamp
from .pilotage import derive_windows
from .policy import VERSION,event_definition_issues


def build_user_policy(snapshot,daylight_path,*,start_at,end_at,night_permissions=None,berth_overrides=None,event_resolution='UNRESOLVED'):
    start,end=timestamp(start_at),timestamp(end_at)
    if end<=start:raise ValueError('Invalid policy coverage')
    raw=Path(daylight_path).read_bytes()
    import io
    rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    selected=[row for row in rows if start.date().isoformat()<=row['date_kst']<=(end-timedelta(seconds=1)).date().isoformat()]
    needed=(end-timedelta(seconds=1)).date()-start.date()
    if len(selected)!=needed.days+1 or len({r['date_kst'] for r in selected})!=len(selected):raise ValueError('Incomplete daylight dates')
    calls=snapshot['calls'];tugs={c['call_id']:1 if number(c['physical']['grt'])<30000 else 2 for c in calls}
    issues=[]
    dock=[]
    from ..integrated_input.validator import tokens
    for group in snapshot['workbook_tables']['groups']['rows']:
        if group['rule_type']=='PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP':dock=tokens(group['berth_ids'])
    policy={'schema_version':VERSION,'policy_id':'USER_SYNTHETIC_60_120_60_LAST_POB_V2','data_origin':'SYNTHETIC_FIXTURE',
      'operational_approval':False,'source_reference':'Explicit user synthetic policy; not official SK rules',
      'daylight_input_sha256':digest(raw),'last_pob_policy':{'below_30000':'NIGHT_IF_BERTH_ALLOWS','30000_to_49999_seconds_before_sunset':5400,'at_least_50000_seconds_before_sunset':7200,'basis':'PILOT_BOARDING','departures':'SAME_RULE'},
      'default_phase_seconds':{'berthing_seconds':3600,'hose_connection_seconds':7200,'unberthing_seconds':3600},
      'tug_capacity':4,'dock_4142_version':'SYNTHETIC_HOSE_END_V1' if dock else 'DISABLED','dock_4142_berths':dock,
      'dock_4142_berthing_resource':'INDEPENDENT','dock_4142_shared_resources':{'4142_SHARED_HANDLING':['HOSE_CONNECT','CARGO','HOSE_DISCONNECT','SWITCHING']},
      'legacy_etu_after_work_seconds':7200,'event_definition_resolution':event_resolution,'berths':{}}
    policy['source_rule_overrides']=[{'source_field':'berthing_maneuver_conflict','source_value':'MUTEX_MANEUVER_ONLY','synthetic_value':'INDEPENDENT_BERTH_MANEUVERS','authority':'EXPLICIT_USER_SYNTHETIC_POLICY'},
                                    {'source_field':'switching_after_previous_work_minutes','source_value':'WORK_END','synthetic_value':'HOSE_DISCONNECTION_END','authority':'EXPLICIT_USER_SYNTHETIC_POLICY'}] if dock else []
    for berth in snapshot['berths']:
        if berth.get('active')!='Y':continue
        bid=berth['berth_id'];config=dict(policy['default_phase_seconds'])
        override=copy.deepcopy((berth_overrides or {}).get(bid,{}))
        if set(override)-set(policy['default_phase_seconds']):raise ValueError('Only explicit phase-duration overrides are supported')
        config.update(override)
        config.update(pilot_boarding_to_berthing_seconds=0,pilot_boarding_to_unberthing_seconds=0,tug_units_by_call=dict(tugs),
                      work_windows=[{'start_at':start.isoformat(),'end_at':end.isoformat()}],pilotage_windows_by_call={})
        permissions=(night_permissions or {}).get(bid,{})
        missing_calls=[]
        for call in calls:
            effective=permissions.get('by_call',{}).get(call['call_id'],permissions)
            if any(type(effective.get(key)) is not bool for key in ('berthing','unberthing')):
                missing_calls.append(call['call_id']);continue
            grt=number(call['physical']['grt']);offset=0 if grt<30000 else 5400 if grt<50000 else 7200
            config['pilotage_windows_by_call'][call['call_id']]={phase:derive_windows(call,selected,night_permitted=effective[phase],last_pob_offset_seconds=offset,last_pob_basis='START') for phase in ('berthing','unberthing')}
        if missing_calls:
            issues.append({'code':'MISSING_PHYSICAL_DATA','berth_id':bid,'call_ids':missing_calls,'field':'individual_berth_night_permissions','reason':'Explicit berth/call conditions take precedence; do not infer ballast/import/draft exceptions from operation direction.'})
        policy['berths'][bid]=config
    issues.extend(event_definition_issues(snapshot,policy))
    return {'status':'POLICY_PENDING' if issues else 'POLICY_INPUTS_VALID','policy':policy,'issues':issues,'operational_approval':False}
