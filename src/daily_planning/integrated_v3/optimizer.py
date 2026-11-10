"""Finite-domain, exact-evaluation V3 search; no operational confirmation.

Caller-supplied contractual timelines must be independently sourced. Minute
settlement limitations are enforced by the existing authoritative evaluator.
"""
import math
import time
from ..integrated_input.provenance import canonical,digest
from ..integrated_input.validator import timestamp
from ..physical_scheduling.service import solve_berth_schedule
from .candidates import generate_candidate_etbs
from .coordinator import validate_candidate
from .settlement import settle_v3_call

CLEAR_BASELINE='CLEAR_WEATHER_BASELINE_V1'

VERSION='V3_VALIDATED_COST_SEARCH_V1'


def _clear_baseline_record(call, row, *, as_of_at):
    """Versioned synthetic no-interruption assumption; not observed weather."""
    start=timestamp(call['nor_tender_at']);finish=timestamp(row['work_end_at']);cursor=start;events=[]
    for raw_a,raw_b in row['work_intervals']:
        a,b=max(start,timestamp(raw_a)),min(finish,timestamp(raw_b))
        if b<=a:continue
        if a<cursor:raise ValueError('Overlapping work intervals')
        if cursor<a:events.append((cursor.isoformat(),a.isoformat(),'WAIT'))
        events.append((a.isoformat(),b.isoformat(),'WORK'));cursor=b
    if cursor<finish:events.append((cursor.isoformat(),finish.isoformat(),'WAIT'))
    return {'call_id':row['call_id'],'work_start_at':row['work_start_at'],'finish_at':row['work_end_at'],
            'known_at':as_of_at,'source_reference':CLEAR_BASELINE,'coverage_attested':True,'event_intervals':events,
            'despatch_policy':'NONE','despatch_policy_reference':'GROSS_DEMURRAGE_ONLY_NO_DESPATCH_CALCULATION',
            'settlement_policy':'SECOND_PRECISION_SIMULATION_V1'}


def _price_schedule(snapshot, rows, records, *, as_of_at, pricing_policy='EXPLICIT_EVENTS_V1'):
    """Use explicitly sourced event records for exactly matching work events."""
    issues=[];settlements={};total=0;asof=timestamp(as_of_at);calls={c['call_id']:c for c in snapshot['calls']}
    if pricing_policy not in ('EXPLICIT_EVENTS_V1',CLEAR_BASELINE):raise ValueError('Unknown pricing policy')
    for row in rows:
        cid=row['call_id']
        matches=([_clear_baseline_record(calls[cid],row,as_of_at=as_of_at)] if pricing_policy==CLEAR_BASELINE else [r for r in records if r.get('call_id')==cid and r.get('work_start_at')==row['work_start_at'] and r.get('finish_at')==row['work_end_at']])
        if len(matches)!=1:
            issues.append({'code':'MISSING_CANDIDATE_SETTLEMENT_EVIDENCE','call_id':cid,'work_start_at':row['work_start_at'],'finish_at':row['work_end_at']});continue
        evidence=matches[0]
        try:
            if not evidence.get('source_reference') or timestamp(evidence['known_at'])>asof or evidence.get('coverage_attested') is not True:
                raise ValueError('Explicit source, known-at time and coverage attestation are required')
            priced=settle_v3_call(snapshot,cid,work_start_at=row['work_start_at'],finish_at=row['work_end_at'],event_intervals=evidence['event_intervals'],despatch_policy=evidence.get('despatch_policy'),despatch_policy_reference=evidence.get('despatch_policy_reference'),settlement_policy=evidence.get('settlement_policy','MINUTE_V1'))
        except (ValueError,KeyError,TypeError) as exc:
            issues.append({'code':'INVALID_SETTLEMENT_EVIDENCE','call_id':cid,'reason':str(exc)});continue
        if priced['status'] not in ('SETTLED_MINUTE_POLICY','SETTLED_SECOND_SIMULATION'):issues.extend(priced['issues']);continue
        if pricing_policy==CLEAR_BASELINE:
            priced['settlement'].pop('despatch_cents',None);priced['settlement'].pop('net_owner_receivable_cents',None)
            priced['gross_demurrage_only']=True;priced['weather_assumption']=CLEAR_BASELINE
        settlements[cid]=priced;total+=priced['settlement']['demurrage_cents']
    return {'status':'SETTLED' if not issues else 'UNRESOLVED','issues':issues,'settlements':settlements,'total_demurrage_cents':total if not issues else None,
            'pending_checks':['INDEPENDENT_EVENT_SOURCE_AND_WEATHER_VERIFICATION']}


def optimize_72h(snapshot, *, as_of_at, policy, settlement_records, commitments=(), completed_rows=(),
                 initial_inventory=None, carried_transfers=None, tank_commitments=None,
                 same_tank_policy=None, previous_schedule=(), candidate_etbs=None,
                 candidate_event_policy='V3_ETB_EVENTS_V1', time_limit_seconds=30, inventory_cuts=False, pricing_policy='EXPLICIT_EVENTS_V1', phase_release_policy='ALL_OPTIONS_V1', inventory_relaxation=False, reuse_physical_model=False):
    """Enumerate complete physical candidates and minimize independently replayed cost.

    The result is only as broad as supplied/generated finite ETBs. Incomplete
    contractual or physical evidence can never become a zero cost or infeasibility
    proof. No candidate is operationally confirmed by this function.
    """
    started=time.monotonic();iterations=[];excluded=[];unresolved=[];incumbent=None;domain_exhausted=False
    def finish(status,**extra):
        nonlocal incumbent
        independent=None
        if incumbent is not None:
            from .independent_validator import validate_simulation_candidate
            independent=validate_simulation_candidate(snapshot,incumbent,policy=policy,as_of_at=as_of_at,commitments=commitments,completed_rows=completed_rows,initial_inventory=initial_inventory,carried_transfers=carried_transfers,tank_commitments=tank_commitments,same_tank_policy=same_tank_policy,settlement_records=settlement_records,pricing_policy=pricing_policy)
            if independent['status']!='VALID':incumbent=None;status='INDEPENDENT_VALIDATION_FAILED'
        return {'model_version':VERSION,'status':status,'elapsed_seconds':time.monotonic()-started,'iterations':iterations,
                'incumbent':incumbent,'independent_validation':independent,'unresolved_candidates':unresolved,'domain_exhausted':domain_exhausted,
                'scope':'Finite generated ETBs and selected phase release options only; no continuous-time optimality','pricing_policy':pricing_policy,'phase_release_policy':phase_release_policy,'inventory_relaxation':inventory_relaxation,'reuse_physical_model':reuse_physical_model,
                'pending_checks':['ACTUAL_WEATHER_AND_EVENTS','CONFIRMATION_AND_FREEZE_AUTHORITY','FULL_ROLLING_COVERAGE'],
                'operational_approval':False,**extra}
    try:
        if isinstance(time_limit_seconds,bool) or not isinstance(time_limit_seconds,(int,float)) or not math.isfinite(time_limit_seconds) or time_limit_seconds<=0:raise ValueError('Invalid time budget')
        if not isinstance(settlement_records,(list,tuple)):raise ValueError('Explicit settlement records required')
        if type(reuse_physical_model) is not bool:raise ValueError('Invalid model reuse policy')
        asof=timestamp(as_of_at);deadline=started+time_limit_seconds
        if candidate_etbs is None:
            generated=generate_candidate_etbs(snapshot,as_of_at=as_of_at,policy=policy,commitments=commitments,previous_schedule=previous_schedule,completed_rows=completed_rows,event_policy=candidate_event_policy,deadline=deadline)
            candidate_etbs=generated['candidate_etbs']
        else:generated=None
        def process(berth):
            nonlocal incumbent
            remaining=deadline-time.monotonic()
            if remaining<=0:return False
            candidate=validate_candidate(snapshot,berth['schedule'],policy=policy,as_of_at=as_of_at,initial_inventory=initial_inventory,carried_transfers=carried_transfers,tank_commitments=tank_commitments,same_tank_policy=same_tank_policy,time_limit_seconds=remaining,inventory_cuts=inventory_cuts,protected_call_ids=[x['call_id'] for x in commitments],inventory_relaxation=inventory_relaxation)
            record={'berth_status':berth['solver_status'],'physical_tank_status':candidate['status'],'tank_status':candidate.get('tank_result',{}).get('status'),'tank_issues':candidate.get('tank_result',{}).get('issues',[])[:5]}
            if candidate['status']=='PHYSICAL_AND_TANK_ONLY':
                pricing=_price_schedule(snapshot,berth['schedule'],settlement_records,as_of_at=as_of_at,pricing_policy=pricing_policy)
                record['settlement_status']=pricing['status'];record['demurrage_cents']=pricing['total_demurrage_cents']
                if pricing['status']=='SETTLED':
                    current={'berth_schedule':berth['schedule'],'tank_validation':candidate['tank_result'],'settlements':pricing['settlements'],'total_demurrage_cents':pricing['total_demurrage_cents'],'source_fingerprint':digest(canonical(snapshot).encode()),'policy_fingerprint':digest(canonical(policy).encode()),'candidate_domain_fingerprint':berth['candidate_domain_fingerprint'],'pricing_policy':pricing_policy,'operational_approval':False}
                    if incumbent is None or current['total_demurrage_cents']<incumbent['total_demurrage_cents']:incumbent=current
                else:unresolved.append({'code':'SETTLEMENT_UNRESOLVED','issues':pricing['issues']})
            elif candidate.get('tank_result',{}).get('status')!='INFEASIBLE':
                unresolved.append({'code':'PHYSICAL_TANK_UNRESOLVED','details':candidate})
            iterations.append(record)
            return time.monotonic()<deadline
        if reuse_physical_model:
            remaining=deadline-time.monotonic()
            if remaining<=0:return finish('TIME_LIMIT_NO_INCUMBENT',candidate_generation=generated)
            berth=solve_berth_schedule(snapshot,as_of_at=as_of_at,candidate_etbs=candidate_etbs,policy=policy,commitments=commitments,completed_rows=completed_rows,time_limit_seconds=remaining,conflict_filter='SAFE_ENVELOPE_V1',phase_release_policy=phase_release_policy,candidate_visitor=process)
            if berth['status']=='ENUMERATION_EXHAUSTED':domain_exhausted=True
            elif berth['status'] in ('TIME_LIMIT_NO_INCUMBENT','ENUMERATION_STOPPED'):
                return finish('TIME_LIMIT',berth_result=berth,candidate_generation=generated)
            elif berth['status']=='INFEASIBLE':domain_exhausted=True
            else:return finish('SEARCH_STOPPED',berth_result=berth,candidate_generation=generated)
        else:
            while time.monotonic()<deadline:
                berth=solve_berth_schedule(snapshot,as_of_at=as_of_at,candidate_etbs=candidate_etbs,policy=policy,commitments=commitments,completed_rows=completed_rows,time_limit_seconds=deadline-time.monotonic(),forbidden_schedules=excluded,conflict_filter='SAFE_ENVELOPE_V1',phase_release_policy=phase_release_policy)
                if berth['status']=='INFEASIBLE':domain_exhausted=True;break
                if berth['status']!='BERTH_SUBSYSTEM_FEASIBLE':
                    return finish('TIME_LIMIT' if berth['status']=='TIME_LIMIT_NO_INCUMBENT' else 'SEARCH_STOPPED',berth_result=berth,candidate_generation=generated)
                if not process(berth):break
                excluded.append(berth['schedule'])
        if incumbent is not None:
            return finish('SIMULATION_FEASIBLE',candidate_generation=generated,best_among_enumerated=(domain_exhausted and not unresolved))
        if unresolved:return finish('UNRESOLVED_NO_VALIDATED_INCUMBENT',candidate_generation=generated)
        return finish('NO_FEASIBLE_CANDIDATE_IN_DOMAIN' if domain_exhausted else 'TIME_LIMIT_NO_INCUMBENT',candidate_generation=generated)
    except TimeoutError as exc:return finish('TIME_LIMIT_NO_INCUMBENT',issues=[{'code':'TIME_BUDGET_EXHAUSTED','reason':str(exc)}])
    except (ValueError,KeyError,TypeError,OverflowError) as exc:return finish('INVALID_INPUT',issues=[{'code':'INVALID_INPUT','reason':str(exc)}])
