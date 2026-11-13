"""Opt-in bounded full-first Rolling admission; never reprice omitted ships as zero.

Fallback is allowed only after an explicit solver-certifiable INFEASIBLE full
attempt. Time limits, unknowns, source-policy gaps and audit failures stop.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime,timezone
from fractions import Fraction
from time import monotonic

DEFERRAL_VERSION = 'V5_ROLLING_SAFE_ADMISSION_V1'


def _timestamp(value):
    t=datetime.fromisoformat(value)
    if t.utcoffset() is None:raise ValueError('Timezone-aware datetime required')
    return t.astimezone(timezone.utc)


def safe_to_defer(call, *, prior_scheduled=False, protected=False):
    return (call.get('fixed_etb_is_hard') is False and
            not prior_scheduled and not protected)


def certified_infeasible(result):
    raw=result.get('optimizer_result') or {}
    if (result.get('status')!='WINDOW_NOT_VALIDATED' or
        result.get('reason')!='INFEASIBLE' or raw.get('solver_status')!='INFEASIBLE'):
        return False
    bad={'UNSUPPORTED_OPERATIONAL_POLICY','POLICY_UNRESOLVED','TIME_BUDGET_EXHAUSTED',
         'INVALID_INPUT','INVALID_FLOW_COVERAGE','INVALID_CARRYOVER_STATE',
         'NO_FEASIBLE_GENERATED_OPTION','FIX_PRECHECK_FAILED','INVALID_COMMITMENT'}
    return not any(row.get('code') in bad for row in
                   list(raw.get('issues') or []) + list(raw.get('rejection_reasons') or [])
                   + list(result.get('issues') or []) if isinstance(row,dict))


def choose_admission(calls, due, *, at, protected=(), previous_schedule=(), optimize,
                     total_budget_seconds, max_probes=8):
    """Evaluate full scope, then a *bounded* latest-ETA-first removal sequence.

    optimize(call_id_list, seconds) must be the unchanged integrated V5 solver.
    No global optimality claim; a bounded unsuccessful search remains blocked.
    """
    due=set(due);protected=set(protected);previous_schedule=set(previous_schedule)
    if not due or not due<=set(calls) or not protected<=due:
        raise ValueError('Invalid due/protected call set')
    if type(max_probes) is not int or not 1<=max_probes<=20:
        raise ValueError('Invalid deferral probe bound')
    budget=float(total_budget_seconds)
    if not 1<=budget<=1800:raise ValueError('Invalid total solver budget')
    _timestamp(at)
    candidates=sorted([cid for cid in due if safe_to_defer(calls[cid],
                           prior_scheduled=cid in previous_schedule,protected=cid in protected)],
                      key=lambda cid:(_timestamp(calls[cid]['eta_at']),cid),reverse=True)
    admitted=set(due);attempts=[];started=monotonic()
    def run(ids,seconds,dropped=None):
        answer=optimize(sorted(ids),seconds)
        if not isinstance(answer,dict):raise ValueError('Invalid optimizer response')
        attempts.append({'admitted_call_ids':sorted(ids),'dropped':dropped,
                         'status':answer.get('status'),'reason':answer.get('reason'),
                         'solver_status':(answer.get('optimizer_result') or {}).get('solver_status')})
        return answer
    current=run(admitted,budget*.5 if candidates else budget)
    if current.get('status')=='VALIDATED':
        return {'status':'VALIDATED','candidate':current,'due':sorted(due),
                'admitted':sorted(admitted),'deferred':[],
                'attempts':attempts,'reason':'FULL_SCOPE_VALIDATED'}
    if not certified_infeasible(current):
        return {'status':'BLOCKED','candidate':current,'attempts':attempts,
                'reason':'FULL_SCOPE_NOT_PROVEN_INFEASIBLE'}
    for dropped in candidates[:max_probes]:
        if len(admitted)<=1:break # Never authorize no-service to claim zero cost.
        remaining=budget-(monotonic()-started)
        probes_left=min(max_probes,len(candidates))-(len(attempts)-1)
        if remaining<=.2 or probes_left<=0:break
        admitted.remove(dropped)
        current=run(admitted,remaining/probes_left,dropped)
        if current.get('status')=='VALIDATED':
            deferred=due-admitted
            if not deferred or not admitted or protected&deferred:
                raise ValueError('Invalid admission partition')
            return {'status':'VALIDATED','candidate':current,'due':sorted(due),
                    'admitted':sorted(admitted),'deferred':sorted(deferred),
                    'attempts':attempts,'reason':'VALIDATED_SAFE_SUBSET',
                    'deferred_cost':'UNPRICED_LIABILITY_NOT_ZERO'}
        if not certified_infeasible(current):break
    mandatory={cid for cid in due if not safe_to_defer(calls[cid],
                 prior_scheduled=cid in previous_schedule,protected=cid in protected)}
    last_reason=('HARD_FIX_OR_PROTECTED_INFEASIBLE'
                 if admitted<=mandatory and certified_infeasible(current) else
                 'NO_VERIFIED_SAFE_SUBSET_IN_BOUNDED_SEARCH')
    return {'status':'BLOCKED','candidate':current,'attempts':attempts,
            'reason':last_reason,
            'protected_or_fix_call_ids':sorted(mandatory)}


def advance_history(calls,prior,*,at,due,admitted,deferred):
    due=set(due);admitted=set(admitted);deferred=set(deferred)
    if admitted&deferred or admitted|deferred!=due:raise ValueError('Invalid coverage partition')
    out=deepcopy(prior)
    for cid in sorted(due):
        if cid in deferred and calls[cid].get('fixed_etb_is_hard') is True:
            raise ValueError('Hard FIX ETB was deferred')
        old=out.get(cid,{})
        count=old.get('carryover_count',0)+int(cid in deferred)
        wait=max(Fraction(0),Fraction(int((_timestamp(at)-_timestamp(calls[cid]['eta_at'])).total_seconds()),3600))
        warnings=[]
        if wait>=48:warnings.append('WAIT_AT_LEAST_48H')
        if count>=2:warnings.append('CARRYOVER_AT_LEAST_2')
        out[cid]={'carryover_count':count,'status':'DEFERRED' if cid in deferred else 'ADMITTED',
                  'last_decision_at':at,'first_due_at':old.get('first_due_at',at),
                  'waiting_hours_exact':str(wait),'warnings':warnings}
    return out


def verify_history(calls,previous,recorded,*,at,due,admitted,deferred,protected,previous_schedule=()):
    due=set(due);admitted=set(admitted);deferred=set(deferred)
    if admitted&deferred or admitted|deferred!=due or set(protected)-admitted:
        raise ValueError('Checkpoint has invalid admission coverage')
    if deferred & set(previous_schedule):
        raise ValueError('Checkpoint deferred a previously scheduled call')
    expectation=advance_history(calls,previous,at=at,due=due,admitted=admitted,deferred=deferred)
    if expectation!=recorded:raise ValueError('Checkpoint deferral history mismatch')
    return expectation
