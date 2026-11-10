"""Bounded feasibility search over explicitly supplied berth candidate times."""
import math
import time
from ..physical_scheduling.service import solve_berth_schedule
from .coordinator import validate_candidate

VERSION='V3_BERTH_TANK_SEARCH_V1'

def search_feasibility(snapshot, *, as_of_at, candidate_etbs, policy, commitments=(),
                       initial_inventory=None, carried_transfers=None, tank_commitments=None,
                       same_tank_policy=None, time_limit_seconds=30, max_iterations=20, inventory_cuts=False, completed_rows=()):
    """Reject only exact complete assignments proved infeasible by tank selection.

    This is not a contract-cost optimizer and never operationally approves a result.
    """
    def result(status, **kw):
        return {'model_version':VERSION,'status':status,'iterations':iterations,'operational_approval':False,
                'scope':'Provided ETBs and generated physical options only; no contract-cost optimality',**kw}
    iterations=[]
    if isinstance(time_limit_seconds,bool) or not isinstance(time_limit_seconds,(int,float)) or not math.isfinite(time_limit_seconds) or time_limit_seconds<=0 or type(max_iterations) is not int or max_iterations<=0:
        return result('INVALID_INPUT',issues=[{'code':'INVALID_BUDGET'}])
    deadline=time.monotonic()+time_limit_seconds;excluded=[]
    for _ in range(max_iterations):
        remaining=deadline-time.monotonic()
        if remaining<=0:return result('TIME_LIMIT_NO_INCUMBENT')
        berth=solve_berth_schedule(snapshot,as_of_at=as_of_at,candidate_etbs=candidate_etbs,policy=policy,commitments=commitments,time_limit_seconds=remaining,forbidden_schedules=excluded,completed_rows=completed_rows)
        if berth['status']!='BERTH_SUBSYSTEM_FEASIBLE':
            return result('CANDIDATE_DOMAIN_EXHAUSTED' if berth['status']=='INFEASIBLE' else 'BERTH_SEARCH_STOPPED',berth_result=berth)
        remaining=deadline-time.monotonic()
        if remaining<=0:return result('TIME_LIMIT_NO_INCUMBENT')
        checked=validate_candidate(snapshot,berth['schedule'],policy=policy,as_of_at=as_of_at,initial_inventory=initial_inventory,carried_transfers=carried_transfers,tank_commitments=tank_commitments,same_tank_policy=same_tank_policy,time_limit_seconds=remaining,inventory_cuts=inventory_cuts)
        iterations.append({'berth_solver_status':berth.get('solver_status'),'candidate_domain_fingerprint':berth.get('candidate_domain_fingerprint'),'validation_status':checked['status'],'tank_status':checked.get('tank_result',{}).get('status')})
        if checked['status']=='PHYSICAL_AND_TANK_ONLY':return result('PHYSICAL_AND_TANK_ONLY',berth_result=berth,validation=checked)
        if checked.get('tank_result',{}).get('status')!='INFEASIBLE':
            return result('UNRESOLVED_VALIDATION',validation=checked)
        excluded.append(berth['schedule'])
    return result('ITERATION_LIMIT_NO_INCUMBENT')


def search_with_events(snapshot, *, as_of_at, policy, previous_schedule=(), commitments=(),
                       initial_inventory=None, carried_transfers=None, tank_commitments=None,
                       same_tank_policy=None, time_limit_seconds=30, max_iterations=20, inventory_cuts=False, completed_rows=()):
    """Generate audited event anchors and run the existing restricted-domain search."""
    from .candidates import generate_candidate_etbs
    started=time.monotonic()
    try:
        generated=generate_candidate_etbs(snapshot,as_of_at=as_of_at,policy=policy,commitments=commitments,previous_schedule=previous_schedule,completed_rows=completed_rows)
        remaining=time_limit_seconds-(time.monotonic()-started)
        if remaining<=0:return {'status':'TIME_LIMIT_NO_INCUMBENT','candidate_generation':generated,'operational_approval':False}
        result=search_feasibility(snapshot,as_of_at=as_of_at,candidate_etbs=generated['candidate_etbs'],policy=policy,commitments=commitments,initial_inventory=initial_inventory,carried_transfers=carried_transfers,tank_commitments=tank_commitments,same_tank_policy=same_tank_policy,time_limit_seconds=remaining,max_iterations=max_iterations,inventory_cuts=inventory_cuts,completed_rows=completed_rows)
        result['candidate_generation']=generated
        return result
    except (ValueError,KeyError,TypeError,OverflowError) as exc:
        return {'status':'INVALID_INPUT','issues':[{'code':'CANDIDATE_GENERATION_FAILED','reason':str(exc)}],'operational_approval':False}
