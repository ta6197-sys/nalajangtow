"""Projection-only rolling trial. Never confirms or modifies operational state."""
from copy import deepcopy
from datetime import timedelta
from ..integrated_input.provenance import canonical,digest
from ..integrated_input.validator import timestamp
from ..physical_scheduling.validator import validate_result
from .search import search_with_events

VERSION='V3_SYNTHETIC_ROLLING_TRIAL_V1'


def simulated_boundary_state(snapshot, prior_result, *, policy, completed_rows=()):
    """Build explicit synthetic state from a validated prior projection.

    The simulation assumes prior plan work through its boundary occurred as
    projected. It is never an observation, approval or database transition.
    """
    if prior_result.get('status')!='PHYSICAL_AND_TANK_ONLY':raise ValueError('Prior plan did not pass subsystem validation')
    checked=prior_result['validation'];tank=checked['tank_result'];rows=prior_result['berth_result']['schedule']
    if checked['status']!='PHYSICAL_AND_TANK_ONLY' or tank['status']!='VALID':raise ValueError('Invalid prior validation')
    if checked['source_fingerprint']!=digest(canonical(snapshot).encode()):raise ValueError('Snapshot changed across boundary')
    if checked['policy_fingerprint']!=digest(canonical(policy).encode()):raise ValueError('Policy changed across boundary')
    if validate_result(snapshot,rows,policy)['status']!='VALID':raise ValueError('Prior physical rows fail replay')
    boundary=timestamp(tank['carry_at']);completed={r['call_id']:deepcopy(r) for r in completed_rows}
    if len(completed)!=len(completed_rows):raise ValueError('Duplicate historical completion')
    locked=[]
    for row in rows:
        cid=row['call_id'];begun=timestamp(row['berthing_start_at'])<boundary
        released=max(timestamp(row['physical_clearance_at']),timestamp(row['switching_end_at']))<=boundary
        if cid in completed:raise ValueError('Completed call returned in prior schedule')
        if begun and released:completed[cid]=deepcopy(row)
        elif begun:locked.append(deepcopy(row))
    locked_ids={r['call_id'] for r in locked}
    carry=tank['carried_transfers']
    if not {r['call_id'] for r in carry}<=locked_ids:raise ValueError('Carried transfer lacks protected berth row')
    needed={r['call_id'] for r in locked if timestamp(r['work_end_at'])>boundary}
    definitions=[deepcopy(x) for x in tank['transfer_definitions'] if x['call_id'] in needed]
    if {x['call_id'] for x in definitions}!=needed:raise ValueError('Missing protected tank transfer')
    if len(tank['carried_inventory_bbl'])!=len(snapshot['tanks']):raise ValueError('Incomplete carried tank inventory')
    return {'boundary':boundary.isoformat(),'completed_rows':list(completed.values()),'commitments':locked,'tank_commitments':definitions,'carried_transfers':deepcopy(carry),'initial_inventory':deepcopy(tank['carried_inventory_bbl']),'previous_schedule':deepcopy(rows),'state_origin':'SIMULATED_PROJECTION_NOT_OBSERVATION','operational_approval':False}


def run_synthetic_trial(snapshot, *, as_of_at, policy, days=7, time_limit_per_day=30, candidate_event_policy='V3_ETB_EVENTS_V1'):
    """Advance validated synthetic projections in 24h increments; stop on first failure."""
    if type(days) is not int or days<1:raise ValueError('days must be a positive integer')
    begin=timestamp(as_of_at);prior=None;state=None;steps=[]
    for index in range(days):
        kwargs={}
        if state is not None:
            kwargs={k:state[k] for k in ('completed_rows','commitments','tank_commitments','carried_transfers','initial_inventory','previous_schedule')}
        result=search_with_events(snapshot,as_of_at=begin.isoformat(),policy=policy,inventory_cuts=True,time_limit_seconds=time_limit_per_day,max_iterations=5,candidate_event_policy=candidate_event_policy,**kwargs)
        summary={'day':index+1,'as_of_at':begin.isoformat(),'status':result['status'],'candidate_count':len(result.get('candidate_generation',{}).get('candidate_etbs',{})),
                 'completed_call_ids':sorted(x['call_id'] for x in kwargs.get('completed_rows',[])),
                 'protected_call_ids':sorted(x['call_id'] for x in kwargs.get('commitments',[])),
                 'modified_crude_calls_in_window':sorted(set(result.get('candidate_generation',{}).get('candidate_etbs',{}))&{'DEM-0144','DEM-0084','DEM-0060'}),
                 'iterations':result.get('iterations',[]),'issues':result.get('issues',[])}
        steps.append({'summary':summary,'result':result})
        if result['status']!='PHYSICAL_AND_TANK_ONLY':break
        if state is not None:
            initial=result['validation']['tank_result']['inventories']
            if any(initial[tid][0]['inventory_bbl']!=value for tid,value in state['initial_inventory'].items()):
                summary['status']='CARRYOVER_MISMATCH';break
        state=simulated_boundary_state(snapshot,result,policy=policy,completed_rows=kwargs.get('completed_rows',[]))
        if timestamp(state['boundary'])!=begin+timedelta(hours=24):raise ValueError('Unexpected rolling boundary')
        prior=result;begin=timestamp(state['boundary'])
    return {'model_version':VERSION,'status':'ALL_REQUESTED_DAYS_SUBSYSTEM_VALID' if len(steps)==days and steps[-1]['summary']['status']=='PHYSICAL_AND_TANK_ONLY' else 'STOPPED_UNRESOLVED',
            'steps':steps,'last_simulated_state':state,'scope':'Synthetic projections only; no observations, approvals, DB writes, contract settlement, weather or operational execution',
            'pending_checks':['FORECAST_INFORMATION_AVAILABILITY','ACTUAL_OBSERVATIONS_AND_APPROVAL','CONTRACT_SETTLEMENT','EXTERNAL_WEATHER','FULL_SCENARIO_AND_OBJECTIVE'], 'operational_approval':False}
