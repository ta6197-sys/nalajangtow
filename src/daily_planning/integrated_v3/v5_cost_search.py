"""Bounded V5 cost enumeration over independently validated schedules."""
import math
import time
from ..integrated_input.provenance import canonical,digest
from .v5_physical import schedule_v5
from .v5_settlement import price_v5_schedule,clock_audit

VERSION='V5_EXACT_COST_ENUMERATION_V1'


def optimize_v5(snapshot, *, data_dir, call_ids, as_of_at, policy, pilotage_context,
                settlement_records=(),pricing_policy='EXPLICIT_EVENTS_V1',clock_policy='BLOCK_UNVERIFIED_CLOCK',
                candidate_generation_mode='AUTO_ADAPTIVE_CANDIDATES',candidate_etbs=None,
                max_cost_candidates=5,time_limit_seconds=30,**schedule_options):
    """Return best exact priced candidate among tested finite-domain schedules."""
    started=time.perf_counter();best=None;evaluated=[];scheduling_runs=[];forbidden=[];seen=set();priced_seen=set();frozen=None;domain=None;unresolved=0;solver_calls=0;stop=None;policy_pending=False;domain_exhausted=False
    def finish(status):
        return {'optimization_status':status,'model_version':VERSION,'pricing_policy':pricing_policy,'settlement_policy':'SECOND_PRECISION_SIMULATION_V1' if pricing_policy=='CLEAR_WEATHER_BASELINE_V1' else 'CANDIDATE_EVIDENCE_DECLARED','clock_policy':clock_policy,
            'evaluated_schedule_count':len(evaluated),'fully_priced_candidate_count':sum(c['settlement_validation']=='SETTLED' for c in evaluated),'unresolved_settlement_count':unresolved,
            'best_candidate_id':best['candidate_id'] if best else None,'best_total_demurrage_cents':best['total_demurrage_cents'] if best else None,
            'vessel_demurrage_breakdown':best['vessel_demurrage_breakdown'] if best else {},'evaluated_candidates':evaluated,'scheduling_runs':scheduling_runs,'best_candidate':best,
            'physical_validation':best['physical_validation'] if best else 'NOT_VALIDATED','inventory_validation':best['inventory_validation'] if best else 'NOT_VALIDATED',
            'settlement_validation':'SETTLED' if best else ('SETTLEMENT_UNRESOLVED' if unresolved else 'NOT_EVALUATED'),
            'search_runtime_seconds':time.perf_counter()-started,'solver_attempt_count':solver_calls,'stopping_reason':stop,
            'optimality_scope':('LOWEST_COST_EXHAUSTED_FINITE_SUPPORTED_DOMAIN' if domain_exhausted and not unresolved and not policy_pending and best else 'LOWEST_FULLY_PRICED_EVALUATED' if best else 'NO_PRICED_INCUMBENT'),
            'candidate_domain_fingerprint':domain,'source_fingerprint':digest(canonical(snapshot).encode()),'clock_start_audit':audit,
            'simulation_only':snapshot.get('source_input',{}).get('data_origin')=='SYNTHETIC_FIXTURE' or pricing_policy=='CLEAR_WEATHER_BASELINE_V1' or clock_policy=='SIMULATION_DERIVED_CONTRACT_TERMS',
            'operational_approval':False,'scope':'Finite candidate enumeration; no global optimality or operational approval'}
    audit=None
    try:
        if type(max_cost_candidates) is not int or max_cost_candidates<1:raise ValueError('Positive integer cost-search candidate budget required')
        if isinstance(time_limit_seconds,bool) or not isinstance(time_limit_seconds,(int,float)) or not math.isfinite(time_limit_seconds) or time_limit_seconds<=0:raise ValueError('Positive finite time budget required')
        if not isinstance(settlement_records,(list,tuple)):raise ValueError('Settlement records must be a list')
        audit=clock_audit(snapshot);deadline=started+time_limit_seconds
        from .v5_physical import _verified_snapshot
        settlement_snapshot=_verified_snapshot(snapshot,data_dir)
        if time.perf_counter()>=deadline:stop='TIME_BUDGET_EXHAUSTED';return finish('NO_VALIDATED_INCUMBENT')
        # Derived bound prevents unlimited physical attempts when all fail inventory.
        inventory_retry_limit=schedule_options.get('max_inventory_reoptimizations',3)
        if type(inventory_retry_limit) is not int or inventory_retry_limit<0:raise ValueError('Invalid inventory retry budget')
        physical_attempt_limit=max_cost_candidates*(inventory_retry_limit+1);physical_examined=0
        while len(evaluated)<max_cost_candidates and physical_examined<physical_attempt_limit:
            remaining=deadline-time.perf_counter()
            if remaining<=0:stop='TIME_BUDGET_EXHAUSTED';break
            kwargs=dict(schedule_options);kwargs.update(data_dir=data_dir,call_ids=call_ids,as_of_at=as_of_at,policy=policy,pilotage_context=pilotage_context,candidate_generation_mode=candidate_generation_mode,candidate_etbs=candidate_etbs,time_limit_seconds=remaining,initial_forbidden_schedules=forbidden)
            if frozen is not None and candidate_generation_mode=='AUTO_ADAPTIVE_CANDIDATES':kwargs['frozen_candidate_domain']=frozen
            run=schedule_v5(snapshot,**kwargs);scheduling_runs.append(run);solver_calls+=run.get('solver_attempt_count',0)
            if time.perf_counter()>=deadline:stop='TIME_BUDGET_EXHAUSTED';break
            if domain is None:
                domain=run.get('candidate_domain_fingerprint');frozen=run.get('candidates_by_call_and_berth') if candidate_generation_mode=='AUTO_ADAPTIVE_CANDIDATES' else None
                policy_pending=any(x.get('code')=='POLICY_UNRESOLVED' for x in run.get('rejection_reasons',[]))
            for item in run.get('reoptimization_attempts',[]):
                rows=item.get('physical_schedule',[]);key=digest(canonical(sorted(rows,key=lambda r:r['call_id'])).encode())
                if key not in seen:seen.add(key);forbidden.append(rows);physical_examined+=1
            if run['status']!='PHYSICAL_AND_INVENTORY_VALID':
                reason=run.get('stopping_reason')
                if run['status']=='TIME_LIMIT_NO_INCUMBENT':stop='TIME_BUDGET_EXHAUSTED';break
                if reason in ('CANDIDATE_DOMAIN_EXHAUSTED','CANDIDATE_DOMAIN_EXHAUSTED_POLICY_PENDING') or (run['status']=='INFEASIBLE' and forbidden):
                    domain_exhausted=True;policy_pending=policy_pending or reason.endswith('POLICY_PENDING');stop='FINITE_DOMAIN_EXHAUSTED';break
                if run['status']=='INVENTORY_VALIDATION_FAILED' and reason=='REOPTIMIZATION_LIMIT_REACHED':continue
                stop=run['status'];break
            rows=run['physical_solver_result']['schedule'];cid=digest(canonical(sorted(rows,key=lambda r:r['call_id'])).encode())
            if cid in priced_seen:stop='DUPLICATE_SCHEDULE_DETECTED';break
            priced_seen.add(cid)
            price=price_v5_schedule(settlement_snapshot,rows,as_of_at=as_of_at,settlement_records=settlement_records,pricing_policy=pricing_policy,clock_policy=clock_policy)
            if time.perf_counter()>=deadline:stop='TIME_BUDGET_EXHAUSTED';break
            record={'candidate_id':cid,'physical_validation':run['physical_validation'],'inventory_validation':run['inventory_validation'],
                    'settlement_validation':price['status'],'total_demurrage_cents':price['total_demurrage_cents'],'vessel_demurrage_breakdown':price['vessel_demurrage_breakdown'],
                    'settlement_issues':price['issues'],'schedule':rows,'schedule_result':run,'candidate_domain_fingerprint':run.get('candidate_domain_fingerprint')}
            evaluated.append(record)
            if price['status']=='SETTLED':
                if best is None or record['total_demurrage_cents']<best['total_demurrage_cents']:best=record
            else:unresolved+=1
        if stop is None:stop='COST_CANDIDATE_LIMIT' if len(evaluated)>=max_cost_candidates else 'PHYSICAL_CANDIDATE_LIMIT'
        return finish('PRICED_INCUMBENT' if best else 'SETTLEMENT_UNRESOLVED' if unresolved else 'NO_VALIDATED_INCUMBENT')
    except (OSError,ValueError,KeyError,TypeError,OverflowError) as exc:
        stop='INVALID_INPUT: '+str(exc);return finish('INVALID_INPUT')
    except Exception as exc:
        stop='SEARCH_ERROR: '+type(exc).__name__+': '+str(exc);return finish('PRICED_INCUMBENT' if best else 'SEARCH_FAILED')
