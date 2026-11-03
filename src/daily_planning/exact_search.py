"""Finite CP-SAT search using valid bounds and exact common-tail evaluation."""
from __future__ import annotations

import math
import time
from datetime import timedelta
from decimal import Decimal

from ortools.sat.python import cp_model

from rolling_horizon import RULE, SHIFTS, baseline, simulate_call

from .context import engine_calls, local_time
from .daylight import simulate_new_call
from .evaluation import evaluate_baseline, interval_overlap, price_until


def build_domain(snapshot, weather):
    """Reuse existing heuristic anchors/shifts without changing bundled caches."""
    begin = local_time(snapshot['as_of_at'])
    end = begin+timedelta(hours=72)
    new_simulate = simulate_new_call if snapshot.get('berthing_policy') == 'DAYLIGHT_WEATHER_V1' else simulate_call
    calls = engine_calls(snapshot)
    fixed_ids = {c['call_id'] for c in snapshot['commits']}
    pending = [c for c in calls if c['call_id'] not in fixed_ids]
    berths = sorted(b['berth_id'] for b in snapshot['berths'] if b['is_active'])
    by_call = {c['call_id']: c for c in calls}
    fixed = []
    for c in snapshot['commits']:
        row = simulate_call(by_call[c['call_id']],local_time(c['start_at']),weather,RULE)
        fixed.append(dict(row,berth_id=c['berth_id']))
    # Existing baseline helpers assume one ongoing job per berth. Collapse only
    # for heuristic seed construction; the actual model keeps every interval.
    occupied = {}
    for r in fixed:
        if r['berth_id'] in berths and (r['berth_id'] not in occupied or r['completion_at'] > occupied[r['berth_id']]['completion_at']):
            occupied[r['berth_id']] = r
    orders = [sorted(pending,key=lambda c:(local_time(c['available_at']),c['call_id'])),
              sorted(pending,key=lambda c:(local_time(c['available_at']).date(),
                     -Decimal(c['contract']['daily_demurrage_usd']),c['call_id'])),
              sorted(pending,key=lambda c:(local_time(c['available_at']).date(),c['processing_minutes'],c['call_id']))]
    seed_rows = [baseline(pending,berths,begin,list(occupied.values()),weather,RULE,
                          [c['call_id'] for c in order],simulate=new_simulate) for order in orders] if pending else [[]]
    anchors = {c['call_id']: [] for c in pending}
    for rows in seed_rows:
        for row in rows:
            anchors[row['call_id']].append(local_time(row['work_start_at']))
    options, groups = [], []
    previous={r['call_id']:r for r in snapshot.get('previous_tentative_assignments',[])}
    for call in pending:
        cid = call['call_id']
        earliest = max(begin,local_time(call['available_at']))
        proposals = {earliest,*anchors[cid]}
        if cid in previous:
            proposals.add(local_time(previous[cid]['start_at']))
        for anchor in anchors[cid][:2]:
            proposals.update(anchor+timedelta(hours=h) for h in SHIFTS)
        proposals.update(local_time(r['completion_at']) for r in fixed)
        variants = {}
        for proposed in sorted(proposals):
            proposed = max(earliest,proposed)
            if proposed >= end:
                continue
            row = new_simulate(call,proposed,weather,RULE)
            if local_time(row['work_start_at']) < end:
                variants[row['work_start_at']] = row
        group = []
        for row in sorted(variants.values(),key=lambda r:r['work_start_at']):
            cost = row['demurrage_cents']-price_until(call,row,begin,weather)['demurrage_cents']
            if cost < 0:
                raise ValueError('Negative future contract increment')
            for bid in berths:
                assigned = dict(row,berth_id=bid)
                if any(interval_overlap(assigned,r) for r in fixed):
                    continue
                group.append(len(options))
                options.append(dict(call_id=cid,row=assigned,cost_lb=cost,
                    wait_lb=int((local_time(row['work_start_at'])-local_time(call['available_at'])).total_seconds()/60)))
        group.append(len(options))
        options.append(dict(call_id=cid,row=None,cost_lb=0,
                            wait_lb=max(0,int((end-local_time(call['available_at'])).total_seconds()/60))))
        groups.append(group)
    lookup = {(o['call_id'],o['row']['berth_id'],o['row']['work_start_at']):j
              for j,o in enumerate(options) if o['row'] is not None}
    deferred = {o['call_id']:j for j,o in enumerate(options) if o['row'] is None}
    seeds = []
    for rows in seed_rows:
        byid = {r['call_id']:r for r in rows}
        seeds.append(tuple(lookup.get((c['call_id'],byid[c['call_id']]['berth_id'],byid[c['call_id']]['work_start_at']),
                                      deferred[c['call_id']]) for c in pending))
    seeds.append(tuple(deferred[c['call_id']] for c in pending))
    fixed_cost = sum(r['demurrage_cents']-price_until(by_call[r['call_id']],r,begin,weather)['demurrage_cents'] for r in fixed)
    fixed_wait = sum(int((local_time(r['work_start_at'])-local_time(by_call[r['call_id']]['available_at'])).total_seconds()/60) for r in fixed)
    return dict(options=options,groups=groups,fixed=fixed,seeds=list(dict.fromkeys(seeds)),
                fixed_cost=fixed_cost,fixed_wait=fixed_wait,begin=begin,berths=berths)


def stability_setup(snapshot,weather,domain):
    previous={r['call_id']:r for r in snapshot.get('previous_tentative_assignments',[])}
    # All replay starts lie inside supplied weather coverage. This upper bound
    # on total shift makes one berth change dominate every possible time shift.
    max_shift=sum(max(abs(int((weather.first-local_time(r['start_at'])).total_seconds()/60)),
                      abs(int((weather.end-local_time(r['start_at'])).total_seconds()/60)))
                  for r in previous.values())
    weight=max_shift+1
    def score(row):
        old=previous.get(row['call_id'])
        if not old:
            return (0,0)
        start=row.get('work_start_at',row.get('start_at'))
        return (int(row['berth_id']!=old['berth_id']),
                abs(int((local_time(start)-local_time(old['start_at'])).total_seconds()/60)))
    coefficients=[]
    for option in domain['options']:
        if option['row'] is None:
            coefficients.append(0)  # Unknown tail assignment: valid lower bound only.
        else:
            changes,shift=score(option['row'])
            coefficients.append(changes*weight+shift)
    fixed=sum(changes*weight+shift for changes,shift in (score(r) for r in domain['fixed']))
    return dict(previous=previous,weight=weight,coefficients=coefficients,fixed=fixed,score=score)


def make_master(domain, objective, cost_cap=None, stability=None):
    model = cp_model.CpModel()
    options = domain['options']
    selected = [model.NewBoolVar(f'option_{j}') for j in range(len(options))]
    intervals = {b:[] for b in domain['berths']}
    for j, option in enumerate(options):
        row = option['row']
        if row is None:
            continue
        start = int((local_time(row['work_start_at'])-domain['begin']).total_seconds()/60)
        length = int((local_time(row['completion_at'])-local_time(row['work_start_at'])).total_seconds()/60)
        intervals[row['berth_id']].append(model.NewOptionalIntervalVar(start,length,start+length,selected[j],f'work_{j}'))
    for i, row in enumerate(domain['fixed']):
        bid = row['berth_id']
        if bid not in intervals:
            continue  # An inactive berth can finish protected work but takes no new work.
        start = int((local_time(row['work_start_at'])-domain['begin']).total_seconds()/60)
        end = int((local_time(row['completion_at'])-domain['begin']).total_seconds()/60)
        intervals[bid].append(model.NewIntervalVar(start,end-start,end,f'fixed_{i}'))
    for values in intervals.values():
        model.AddNoOverlap(values)
    for group in domain['groups']:
        model.AddExactlyOne(selected[j] for j in group)
    cost = domain['fixed_cost']+sum(o['cost_lb']*selected[j] for j,o in enumerate(options))
    if cost_cap is not None:
        model.Add(cost <= cost_cap)
    if objective == 'cost':
        lower_expression = cost
    elif objective == 'wait':
        lower_expression = domain['fixed_wait']+sum(o['wait_lb']*selected[j] for j,o in enumerate(options))
    elif objective == 'stability':
        lower_expression=stability['fixed']+sum(v*selected[j] for j,v in enumerate(stability['coefficients']))
    else:
        raise ValueError(f'Unsupported search objective: {objective}')
    model.Minimize(lower_expression)
    return model,selected,lower_expression


def exact_search(snapshot,weather,domain,*,objective='cost',time_limit=180,cost_cap=None,
                 excluded_signatures=(),max_evaluations=None):
    """Minimize exact replay cost/wait over the finite domain, with proof tracking.

    Master bounds omit unknown tail costs; evaluated schedules never do.
    Already evaluated choices are excluded. Bounds apply to unexplored choices.
    """
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('Time limit must be positive and finite')
    started=time.monotonic()
    stability=stability_setup(snapshot,weather,domain) if objective=='stability' else None
    model,x,expression=make_master(domain,objective,cost_cap,stability)
    error=model.Validate()
    if error:
        raise ValueError(error)
    excluded=set(excluded_signatures)
    seen=set()
    incumbent=None
    best=None
    trace=[]
    if objective=='stability':
        lower=stability['fixed']+sum(min(stability['coefficients'][j] for j in g) for g in domain['groups'])
    else:
        lower = (domain['fixed_cost'] if objective=='cost' else domain['fixed_wait'])
        lower += sum(min(domain['options'][j][objective+'_lb'] for j in g) for g in domain['groups'])
    exhausted=False
    cp_sat_calls=0
    status='NOT_STARTED'
    def inspect_choice(choice, source):
        nonlocal incumbent,best
        if choice in seen:
            return
        seen.add(choice)
        model.AddBoolOr([x[j].Not() for j in choice])
        rows=[domain['options'][j]['row'] for j in choice if domain['options'][j]['row'] is not None]
        # Seed heuristics can fail model constraints; reject them before replay.
        all_rows=domain['fixed']+rows
        if any(interval_overlap(a,b) for i,a in enumerate(all_rows) for b in all_rows[i+1:]):
            return
        result=evaluate_baseline(snapshot,rows,weather)
        summary=result['evaluation']['summary']
        cost=summary['demurrage_increment_to_completion_cents']
        if objective=='stability':
            parts=[stability['score'](r) for r in result['evaluation']['vessel_results']]
            changes=sum(v[0] for v in parts)
            shift=sum(v[1] for v in parts)
            score=changes*stability['weight']+shift
            summary.update(berth_change_count=changes,start_shift_minutes=shift,
                           compared_tentative_call_count=len(stability['previous']))
        else:
            score=cost if objective=='cost' else summary['total_wait_minutes']
        allowed=result['schedule_signature'] not in excluded and (cost_cap is None or cost<=cost_cap)
        trace.append(dict(evaluation_index=len(trace)+1,source=source,exact_cost_cents=cost,
                          exact_objective=score,accepted=allowed,schedule_signature=result['schedule_signature']))
        if allowed and (best is None or (score,cost,summary['total_wait_minutes'],result['schedule_signature'])
                        < (best,incumbent['evaluation']['summary']['demurrage_increment_to_completion_cents'],
                           incumbent['evaluation']['summary']['total_wait_minutes'],incumbent['schedule_signature'])):
            best,incumbent=score,result
    for choice in domain['seeds']:
        if time.monotonic()-started >= time_limit or (max_evaluations is not None and len(trace)>=max_evaluations):
            break
        inspect_choice(choice,'HEURISTIC_SEED')
    while time.monotonic()-started < time_limit:
        if incumbent is not None and lower>=best:
            status='BOUND_PROOF'
            break
        if max_evaluations is not None and len(trace)>=max_evaluations:
            status='EVALUATION_LIMIT'
            break
        if incumbent is not None:
            model.Add(expression <= best-1)
        solver=cp_model.CpSolver()
        solver.parameters.max_time_in_seconds=max(0.001,time_limit-(time.monotonic()-started))
        solver.parameters.num_search_workers=8
        solver.parameters.random_seed=42
        result=solver.Solve(model)
        cp_sat_calls+=1
        status=solver.StatusName(result)
        if result==cp_model.INFEASIBLE:
            exhausted=True
            break
        if result not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
            break
        # A nonnegative integer objective is bounded below by the floored solver
        # bound. Flooring avoids inflating a bound because of float precision.
        lower=max(lower,math.floor(solver.BestObjectiveBound()))
        if incumbent is not None and lower>=best:
            status='BOUND_PROOF'
            break
        choice=tuple(next(j for j in g if solver.Value(x[j])) for g in domain['groups'])
        inspect_choice(choice,'CP_SAT')
    proven=incumbent is not None and (exhausted or lower>=best)
    if proven:
        lower=best
        if status=='NOT_STARTED':
            status='BOUND_PROOF'
    elif incumbent is not None:
        lower=min(lower,best)
    record=dict(objective=objective,solver_status='OPTIMAL' if proven else ('FEASIBLE' if incumbent else ('INFEASIBLE' if exhausted else 'UNKNOWN')),
                termination=status,seconds=round(time.monotonic()-started,6),time_limit_seconds=time_limit,
                exact_objective=best,valid_lower_bound=lower,
                relative_gap=None if best is None else max(0,(best-lower)/max(1,abs(best))),
                option_count=len(domain['options']),evaluations=len(trace),cp_sat_calls=cp_sat_calls,cost_cap_cents=cost_cap,
                scope='Finite generated start/berth options; exact FCFS evaluation tail; not all minute-level starts',
                master_objective='Proven lower bound on unexplored schedules; not a simulated final cost',trace=trace)
    if stability:
        record.update(stability_priority='BERTH_CHANGE_COUNT_THEN_START_SHIFT_MINUTES',
                      stability_time_shift_weight=stability['weight'],
                      compared_tentative_call_count=len(stability['previous']))
    return incumbent,record


def cost_then_wait_search(snapshot, weather, domain, *, time_limit=180):
    """Lexicographic A search within one time budget."""
    first, primary = exact_search(snapshot, weather, domain, time_limit=time_limit)
    if first is None:
        return first, primary
    remaining = time_limit - primary['seconds']
    if remaining <= 0.01:
        primary['tie_break_status'] = 'TIME_LIMIT'
        return first, primary
    tied, secondary = exact_search(snapshot, weather, domain, objective='wait',
        cost_cap=first['evaluation']['summary']['demurrage_increment_to_completion_cents'],
        time_limit=remaining)
    if tied is None:
        primary['tie_break_status'] = 'NO_FEASIBLE_RESULT'
        return first, primary
    secondary['objective'] = 'cost_then_wait'
    secondary['primary_cost_search'] = dict(status=primary['solver_status'],
        seconds=primary['seconds'], valid_lower_bound=primary['valid_lower_bound'],
        relative_gap=primary['relative_gap'])
    if primary['solver_status'] != 'OPTIMAL':
        secondary['solver_status'] = 'FEASIBLE'
    return tied, secondary
