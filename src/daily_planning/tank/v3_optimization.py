"""V3 tank selection; every MILP proposal is checked by exact inventory replay."""
import time
import math
import json
import subprocess
import sys
from .v3_inventory import validate_v3_inventory
from ..integrated_input.validator import tokens


def solve_v3_allocation(snapshot, transfers, *, start_at, end_at, initial_inventory=None,
                        same_tank_policy=None, carried_transfers=None, commitments=None, time_limit_seconds=30):
    """Solve in an isolated HiGHS process to avoid OR-Tools native-library collisions."""
    if isinstance(time_limit_seconds,bool) or not isinstance(time_limit_seconds,(int,float)) or not math.isfinite(time_limit_seconds) or time_limit_seconds<=0:
        return {'status':'INVALID_INPUT','issues':[{'code':'INVALID_TIME_LIMIT'}],'operational_approval':False}
    if all(x.get('tank_id') for x in transfers):
        result=validate_v3_inventory(snapshot,transfers,start_at=start_at,end_at=end_at,initial_inventory=initial_inventory,same_tank_policy=same_tank_policy,carried_transfers=carried_transfers,commitments=commitments)
        result['solver_status']='NOT_NEEDED_FIXED_ASSIGNMENTS'
        return result
    try:
        payload={'snapshot':snapshot,'transfers':transfers,'start_at':start_at,'end_at':end_at,'initial_inventory':initial_inventory,'same_tank_policy':same_tank_policy,'carried_transfers':carried_transfers,'commitments':commitments,'time_limit_seconds':time_limit_seconds}
        process=subprocess.run([sys.executable,'-m','src.daily_planning.tank.v3_optimization'],input=json.dumps(payload),capture_output=True,text=True,timeout=float(time_limit_seconds),check=False)
        if process.returncode: return {'status':'SOLVER_UNAVAILABLE','issues':[{'code':'SOLVER_PROCESS_FAILED','reason':process.stderr[-2000:]}],'operational_approval':False}
        return json.loads(process.stdout)
    except subprocess.TimeoutExpired:
        return {'status':'TIME_LIMIT_NO_INCUMBENT','issues':[{'code':'SOLVER_PROCESS_TIMEOUT'}],'operational_approval':False}
    except (ValueError,TypeError,OSError) as exc:
        return {'status':'INVALID_INPUT','issues':[{'code':'INVALID_INPUT','reason':str(exc)}],'operational_approval':False}


def _solve_v3_allocation(snapshot, transfers, *, start_at, end_at, initial_inventory=None,
                         same_tank_policy=None, carried_transfers=None, commitments=None, time_limit_seconds=30):
    """Find single-tank assignments within provided eligible choices, without changing times.

    Each input transfer may provide tank_id, otherwise eligible_tank_ids. The
    MILP enumerates assignments with exact no-good cuts; inventory is independently
    checked with rational arithmetic. No optimality of a full schedule is implied.
    """
    if all(x.get('tank_id') for x in transfers):
        result=validate_v3_inventory(snapshot,transfers,start_at=start_at,end_at=end_at,initial_inventory=initial_inventory,same_tank_policy=same_tank_policy,carried_transfers=carried_transfers,commitments=commitments)
        result['solver_status']='NOT_NEEDED_FIXED_ASSIGNMENTS'
        return result
    try:
        import pyomo.environ as pyo
        from pyomo.opt import TerminationCondition
        if time_limit_seconds<=0:raise ValueError('Positive time limit required')
        tanks={t['tank_id']:t for t in snapshot['tanks']};berths={b['berth_id']:b for b in snapshot['berths']};calls={c['call_id']:c for c in snapshot['calls']}
        pairs=[]
        for i,x in enumerate(transfers):
            c=calls[x['call_id']];b=berths[x['berth_id']]
            choices=[x['tank_id']] if x.get('tank_id') else x['eligible_tank_ids']
            choices=list(dict.fromkeys(choices))
            eligible=[k for k in choices if k in tanks and k in tokens(b['connected_tank_ids']) and x['berth_id'] in tokens(tanks[k]['connected_berth_ids']) and tanks[k]['cargo_group']==c['cargo_group']]
            if not eligible:raise ValueError('No eligible connected tank for '+x['call_id'])
            pairs.extend((i,k) for k in eligible)
        model=pyo.ConcreteModel();model.P=pyo.Set(dimen=2,initialize=pairs,ordered=True);model.y=pyo.Var(model.P,domain=pyo.Binary);model.constraints=pyo.ConstraintList()
        for i in range(len(transfers)):model.constraints.add(sum(model.y[j,k] for j,k in pairs if j==i)==1)
        model.objective=pyo.Objective(expr=0);solver=pyo.SolverFactory('appsi_highs');started=time.monotonic();last=None;attempts=0
        while time.monotonic()-started<time_limit_seconds:
            solver.options['time_limit']=max(0.000001,time_limit_seconds-(time.monotonic()-started))
            answer=solver.solve(model,load_solutions=False);termination=answer.solver.termination_condition
            if termination==TerminationCondition.infeasible:
                return {'model_version':'V3_TANK_ALLOCATION_V1','status':'INFEASIBLE','solver_status':'INFEASIBLE_WITHIN_PROVIDED_TANK_CHOICES','issues':(last or {}).get('issues',[]),'attempts':attempts,'operational_approval':False}
            if termination not in (TerminationCondition.optimal,TerminationCondition.feasible):break
            model.solutions.load_from(answer)
            chosen=[(i,k) for i,k in pairs if pyo.value(model.y[i,k])>0.5];assigned=[dict(x) for x in transfers]
            for i,k in chosen:assigned[i]['tank_id']=k
            result=validate_v3_inventory(snapshot,assigned,start_at=start_at,end_at=end_at,initial_inventory=initial_inventory,same_tank_policy=same_tank_policy,carried_transfers=carried_transfers,commitments=commitments);attempts+=1
            if result['status']=='VALID':
                result.update(solver_status=str(termination),allocation_model_version='V3_TANK_ALLOCATION_V1',attempts=attempts);return result
            if result['status']=='INVALID_INPUT' or any(x['code']=='PENDING_SAME_TANK_CONCURRENCY' for x in result['issues']):
                result.update(solver_status=str(termination),allocation_model_version='V3_TANK_ALLOCATION_V1',attempts=attempts);return result
            last=result;model.constraints.add(sum(model.y[i,k] for i,k in chosen)<=len(chosen)-1)
        return {'model_version':'V3_TANK_ALLOCATION_V1','status':'TIME_LIMIT_NO_INCUMBENT','issues':(last or {}).get('issues',[]),'attempts':attempts,'operational_approval':False}
    except ImportError as exc:return {'model_version':'V3_TANK_ALLOCATION_V1','status':'SOLVER_UNAVAILABLE','issues':[{'code':'SOLVER_UNAVAILABLE','reason':str(exc)}],'operational_approval':False}
    except (ValueError,KeyError,TypeError,RuntimeError) as exc:return {'model_version':'V3_TANK_ALLOCATION_V1','status':'INVALID_INPUT','issues':[{'code':'INVALID_INPUT','reason':str(exc)}],'operational_approval':False}


if __name__=='__main__':
    payload=json.load(sys.stdin)
    print(json.dumps(_solve_v3_allocation(**payload)))
