"""Exact whole-schedule exclusions for V5 inventory-aware candidate search."""
from copy import deepcopy
import time
from ..integrated_input.provenance import canonical,digest
from ..integrated_input.validator import timestamp,number

RETRYABLE={'INVENTORY_OUT_OF_BOUNDS','RECEIPT_RATE_EXCEEDED','DISPATCH_RATE_EXCEEDED'}


def _violations(evaluation):
    issues=deepcopy(evaluation.get('issues',[]));tank_result=(evaluation.get('inventory_result') or {}).get('tank_result') or {}
    assignments=tank_result.get('assignments',[])
    for issue in issues:
        tid=issue.get('tank_id');at=issue.get('at');related=set(issue.get('call_ids',[]))
        limits=evaluation.get('tank_limits',{}).get(tid,{})
        if issue.get('code')=='INVENTORY_OUT_OF_BOUNDS' and issue.get('inventory_bbl') is not None and limits:
            value=number(issue['inventory_bbl'])
            if value<number(limits['min_inventory_bbl']):issue.update(constraint='MINIMUM_INVENTORY',limit_bbl=str(limits['min_inventory_bbl']))
            elif value>number(limits['capacity_bbl']):issue.update(constraint='MAXIMUM_CAPACITY',limit_bbl=str(limits['capacity_bbl']))
        elif issue.get('code')=='RECEIPT_RATE_EXCEEDED':issue['constraint']='GROSS_RECEIPT_RATE'
        elif issue.get('code')=='DISPATCH_RATE_EXCEEDED':issue['constraint']='GROSS_DISPATCH_RATE'
        if issue.get('call_id'):related.add(issue['call_id'])
        active=set()
        for transfer in assignments:
            if transfer.get('tank_id')!=tid:continue
            related.add(transfer['call_id'])
            if at is not None and timestamp(transfer['start_at'])<=timestamp(at)<=timestamp(transfer['end_at']):active.add(transfer['call_id'])
        if related:issue['related_call_ids']=sorted(related)
        if active:issue['active_call_ids']=sorted(active)
    return issues


def search(initial_physical, *, evaluate, solve_alternative, deadline, max_reoptimizations,initial_forbidden_schedules=()):
    """Re-solve the same finite domain, excluding only exact rejected schedules."""
    started=time.perf_counter();attempts=[];forbidden=deepcopy(list(initial_forbidden_schedules));seen={digest(canonical(sorted(rows,key=lambda r:r['call_id'])).encode()) for rows in forbidden};physical=initial_physical;last_eval=None;last_search=None;solver_calls=1
    def result(status,reason):
        return {'status':status,'stopping_reason':reason,'attempts':attempts,'forbidden_schedules':forbidden,'physical':physical,'evaluation':last_eval,'last_search_result':last_search,'solver_calls':solver_calls}
    while True:
        if time.perf_counter()>=deadline:return result('TIME_LIMIT_NO_INCUMBENT','TIME_BUDGET_EXHAUSTED')
        rows=physical['schedule'];key=digest(canonical(sorted(rows,key=lambda r:r['call_id'])).encode())
        if key in seen:return result('INTEGRATION_VALIDATION_FAILED','DUPLICATE_SCHEDULE_DETECTED')
        seen.add(key);last_eval=evaluate(rows)
        inventory_issues=_violations(last_eval) if last_eval.get('status')=='INVENTORY_VALIDATION_FAILED' else []
        attempts.append({'attempt':len(attempts)+1,'schedule_fingerprint':key,'physical_solver_status':physical.get('solver_status',physical['status']),'physical_validation':deepcopy(last_eval.get('physical_validation')),'inventory_validation_status':last_eval.get('inventory_validation','NOT_EVALUATED'),'inventory_violations':inventory_issues,'selected_berths':deepcopy(last_eval.get('selected_berths',{})),'selected_etbs':deepcopy(last_eval.get('selected_etbs',{})),'physical_schedule':deepcopy(rows),'inventory_result':deepcopy(last_eval.get('inventory_result')),'rejection_reason':None if last_eval['status']=='PHYSICAL_AND_INVENTORY_VALID' else last_eval['status'],'elapsed_seconds':time.perf_counter()-started})
        if time.perf_counter()>=deadline:return result('TIME_LIMIT_NO_INCUMBENT','TIME_BUDGET_EXHAUSTED')
        if last_eval['status']=='PHYSICAL_AND_INVENTORY_VALID':return result('PHYSICAL_AND_INVENTORY_VALID','COMBINED_VALID')
        if last_eval['status']!='INVENTORY_VALIDATION_FAILED':return result(last_eval['status'],'NON_INVENTORY_VALIDATION_FAILURE')
        if not inventory_issues or any(issue.get('code') not in RETRYABLE for issue in inventory_issues):return result('INVENTORY_VALIDATION_FAILED','NON_RETRYABLE_INVENTORY_FAILURE')
        if len(attempts)>max_reoptimizations:return result('INVENTORY_VALIDATION_FAILED','REOPTIMIZATION_LIMIT_REACHED')
        forbidden.append(deepcopy(rows));remaining=deadline-time.perf_counter()
        if remaining<=0:return result('TIME_LIMIT_NO_INCUMBENT','TIME_BUDGET_EXHAUSTED')
        solver_calls+=1;last_search=solve_alternative(forbidden,remaining)
        if time.perf_counter()>=deadline:return result('TIME_LIMIT_NO_INCUMBENT','TIME_BUDGET_EXHAUSTED')
        if last_search.get('status')=='TIME_LIMIT_NO_INCUMBENT':return result('TIME_LIMIT_NO_INCUMBENT','TIME_BUDGET_EXHAUSTED')
        if last_search.get('status')=='INFEASIBLE':return result('INVENTORY_VALIDATION_FAILED','CANDIDATE_DOMAIN_EXHAUSTED')
        if last_search.get('status')!='BERTH_SUBSYSTEM_FEASIBLE':return result('INTEGRATION_VALIDATION_FAILED','PHYSICAL_REOPTIMIZATION_FAILED')
        physical=last_search
