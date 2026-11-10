"""Synthetic costed rolling validation; never confirms operational records."""
from datetime import timedelta
from ..integrated_input.validator import timestamp
from .optimizer import optimize_72h, CLEAR_BASELINE
from .synthetic_rolling import simulated_boundary_state

VERSION='V3_COSTED_ROLLING_SIMULATION_V1'


def run_costed_rolling(snapshot, *, as_of_at, policy, days, time_limit_per_day=30,
                       candidate_policies=('V3_ETB_EVENTS_V1','V3_ETB_FLOW_EVENTS_V2')):
    """Advance only validated synthetic candidates, stopping at first unresolved day."""
    if type(days) is not int or days<1 or not candidate_policies:raise ValueError('Positive days and candidate policies required')
    begin=timestamp(as_of_at);state=None;steps=[]
    for index in range(days):
        kwargs={}
        if state is not None:kwargs={k:state[k] for k in ('completed_rows','commitments','tank_commitments','carried_transfers','initial_inventory','previous_schedule')}
        trials=[];chosen=None
        for candidate_policy in candidate_policies:
            result=optimize_72h(snapshot,as_of_at=begin.isoformat(),policy=policy,settlement_records=[],pricing_policy=CLEAR_BASELINE,inventory_cuts=True,time_limit_seconds=time_limit_per_day,candidate_event_policy=candidate_policy,**kwargs)
            trials.append({'candidate_policy':candidate_policy,'status':result['status'],'elapsed_seconds':result['elapsed_seconds'],'iterations':len(result['iterations']),'unresolved_count':len(result['unresolved_candidates'])})
            if result['incumbent'] is not None:chosen=result;break
        step={'day':index+1,'as_of_at':begin.isoformat(),'trials':trials,'result':chosen or result,
              'completed_call_ids':sorted(x['call_id'] for x in kwargs.get('completed_rows',[])),
              'protected_call_ids':sorted(x['call_id'] for x in kwargs.get('commitments',[]))}
        steps.append(step)
        if chosen is None:break
        candidate=chosen['incumbent'];tank=candidate['tank_validation']
        if state is not None and any(tank['inventories'][tid][0]['inventory_bbl']!=amount for tid,amount in state['initial_inventory'].items()):
            step['error']='CARRYOVER_MISMATCH';break
        synthetic={'status':'PHYSICAL_AND_TANK_ONLY','berth_result':{'schedule':candidate['berth_schedule']},
                   'validation':{'status':'PHYSICAL_AND_TANK_ONLY','source_fingerprint':candidate['source_fingerprint'],'policy_fingerprint':candidate['policy_fingerprint'],'tank_result':tank}}
        state=simulated_boundary_state(snapshot,synthetic,policy=policy,completed_rows=kwargs.get('completed_rows',[]))
        if timestamp(state['boundary'])!=begin+timedelta(hours=24):raise ValueError('Unexpected rolling boundary')
        begin=timestamp(state['boundary'])
    return {'model_version':VERSION,'status':'REQUESTED_WINDOWS_VALIDATED' if len(steps)==days and steps[-1]['result']['incumbent'] is not None and 'error' not in steps[-1] else 'STOPPED_UNRESOLVED',
            'steps':steps,'last_simulated_state':state,'simulation_policy':CLEAR_BASELINE,
            'scope':'Each 72h window separately costed; no cumulative settlement, observation, operational confirmation or full-horizon optimality',
            'operational_approval':False}
