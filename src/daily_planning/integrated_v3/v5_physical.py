"""V5 finite-domain physical scheduling followed by exact inventory validation.

The workbook and snapshot are verified before their physical tables are used.
Only explicitly selected calls are optimized; source calls are never deleted.
"""
from copy import deepcopy
from datetime import timedelta
import io
import math
from pathlib import Path
import time
from ..integrated_input.loader import load_inputs
from ..integrated_input.normalizer import normalize
from ..integrated_input.provenance import canonical,digest,cell
from ..integrated_input.validator import timestamp,tokens
from ..integrated_input.v5 import validate_v5,eligible_berth_ids
from ..physical_scheduling.service import solve_berth_schedule,_V5_VALIDATED_CONTEXT
from ..physical_scheduling.validator import validate_result
from ..physical_scheduling.policy import validate_policy
from ..physical_scheduling.v5_user_rules import enabled as user_v5, DOCK_PAIR
from .coordinator import validate_v5_candidate
from .v5_pilotage import prepare_pilotage_policy

VERSION='V5_PHYSICAL_INVENTORY_CHECKPOINT_V1'


def _verified_snapshot(snapshot,data_dir):
    bundle=load_inputs(data_dir,scenario='V5_3TANK')
    expected=normalize(bundle)
    if canonical(snapshot)!=canonical(expected):raise ValueError('Snapshot differs from verified original inputs')
    preflight=validate_v5(snapshot)
    if not preflight['data_preflight_pass']:raise ValueError('V5 preflight failed: '+str(preflight['errors']))
    root=Path(data_dir);item=bundle['artifacts'][0];path=root/item['filename'];raw=path.read_bytes()
    if digest(raw)!=item['sha256']:raise ValueError('Workbook fingerprint changed during loading')
    from openpyxl import load_workbook
    wb=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
    tables={}
    try:
        for key,name,required in [('groups','15_선석그룹제약',{'rule_id','rule_type','berth_ids'}),('restrictions','16_선석제약시간',{'restriction_id','berth_id','start_local','end_local','restriction_scope'})]:
            if name not in wb.sheetnames:raise ValueError('Missing required source sheet '+name)
            it=wb[name].iter_rows(values_only=True);headers=list(next(it,()))
            if not required<=set(headers):raise ValueError('Missing required source headers: '+name)
            rows=[]
            for n,values in enumerate(it,2):
                if any(v is not None for v in values):
                    row={h:cell(v) for h,v in zip(headers,values) if h is not None};row['_source_row']=n;rows.append(row)
            tables[key]={'sheet':name,'headers':[h for h in headers if h], 'rows':rows}
    finally:wb.close()
    result=deepcopy(snapshot);result['workbook_tables'].update(tables)
    return result


def schedule_v5(snapshot, *, data_dir, call_ids, as_of_at, candidate_etbs=None, policy,
                time_limit_seconds=30, projection_end_at=None, initial_inventory=None,
                carried_transfers=None, tank_commitments=None, pilotage_context=None,
                candidate_generation_mode='MANUAL_CANDIDATES',max_expansion_rounds=3,
                max_candidates_per_pair=12,max_total_candidates=2000,physical_commitments=(),max_inventory_reoptimizations=3,
                initial_forbidden_schedules=(),frozen_candidate_domain=None,objective_mode='FEASIBILITY',
                pricing_policy='EXPLICIT_EVENTS_V1',clock_policy='BLOCK_UNVERIFIED_CLOCK',settlement_records=(),inventory_constraint_mode='NONE',candidate_improvement_mode='NONE',max_improvement_rounds=2,tie_break_mode='NONE'):
    """Validate and schedule a finite V5 subset; no schedule-wide feasibility claim."""
    started=time.perf_counter();valid_budget=isinstance(time_limit_seconds,(int,float)) and not isinstance(time_limit_seconds,bool) and math.isfinite(time_limit_seconds) and time_limit_seconds>0;deadline=started+(float(time_limit_seconds) if valid_budget else 0);candidate_etbs=candidate_etbs or {};domain={'as_of_at':as_of_at,'call_ids':sorted(call_ids),'candidate_etbs':candidate_etbs}
    result={'scenario_id':'V5_3TANK','model_version':VERSION,'source_fingerprint':digest(canonical(snapshot).encode()),
            'policy_fingerprint':digest(canonical(policy).encode()),'candidate_domain_fingerprint':digest(canonical(domain).encode()),
            'solver_status':'NOT_RUN','physical_validation':'NOT_EVALUATED','inventory_validation':'NOT_EVALUATED',
            'combined_validation':'NOT_VALIDATED','scheduled_calls':[],'unscheduled_calls':[],
            'selected_berths':{},'selected_etbs':{},'inventory_result':None,'issues':[],
            'operational_approval':False,'scope':'Selected calls and supplied finite ETB candidates only; full scheduling and settlement not evaluated'}
    result.update(candidate_generation_mode=candidate_generation_mode,initial_candidate_count=0,final_candidate_count=0,candidates_by_call_and_berth={},generation_rounds=[],generation_reasons={},rejection_reasons=[],expansion_events=[],stopping_reason=None)
    result.update(reoptimization_attempts=[],inventory_violations=[],forbidden_schedule_count=0,inventory_constraints_added=[],physical_solver_status='NOT_RUN',inventory_validation_status='NOT_EVALUATED',final_inventory_result=None,solver_attempt_count=0,inventory_reoptimization_solver_calls=0)
    pool=None;manual_domain_fingerprint=None
    def finish(status,code=None,reason=None,**details):
        result['status']=status
        if code:result['issues'].append({'code':code,'reason':str(reason),**details})
        if pool is not None:
            result['final_candidate_count']=pool.count();result['candidates_by_call_and_berth']=pool.domain();result['generation_reasons']=pool.reasons();result['rejection_reasons'].extend(pool.rejected);result['expansion_events']=pool.expansion_events
            result['candidate_domain_fingerprint']=digest(canonical({'as_of_at':as_of_at,'call_ids':sorted(call_ids),'pair_candidate_etbs':pool.domain(),'physical_commitments':list(physical_commitments)}).encode())
        elif manual_domain_fingerprint is not None:result['candidate_domain_fingerprint']=manual_domain_fingerprint
        if result['stopping_reason'] is None:result['stopping_reason']=status
        result['runtime_seconds']=time.perf_counter()-started
        return result
    try:
        if not valid_budget:return finish('INVALID_INPUT','INVALID_TIME_BUDGET','Positive finite time budget required')
        result['candidate_improvement_mode']=candidate_improvement_mode
        if tie_break_mode not in ('NONE','CANONICAL_ETB_BERTH_V1'):return finish('INVALID_INPUT','INVALID_TIE_BREAK_MODE','Unknown tie-break mode')
        if tie_break_mode!='NONE' and (objective_mode!='MIN_TOTAL_DEMURRAGE' or inventory_constraint_mode!='EXACT_EVENT_BALANCE_V1' or policy.get('schema_version')!='V5_USER_ETU_SWITCHING_20261112_V1'):
            return finish('INVALID_INPUT','INVALID_TIE_BREAK_MODE','Tie-break requires integrated cost and exact inventory')
        if tie_break_mode!='NONE':result['tie_break_mode']=tie_break_mode
        if candidate_improvement_mode not in ('NONE','COST_AWARE_V1'):return finish('INVALID_INPUT','INVALID_IMPROVEMENT_MODE','Unknown candidate improvement mode')
        if candidate_improvement_mode=='COST_AWARE_V1' and (candidate_generation_mode!='AUTO_ADAPTIVE_CANDIDATES' or objective_mode!='MIN_TOTAL_DEMURRAGE' or inventory_constraint_mode!='EXACT_EVENT_BALANCE_V1' or frozen_candidate_domain is not None or type(max_improvement_rounds) is not int or not 0<=max_improvement_rounds<=2):return finish('INVALID_INPUT','INVALID_IMPROVEMENT_MODE','Cost-aware expansion requires automatic candidates, integrated cost mode, an unfrozen domain, and at most two rounds')
        if candidate_improvement_mode=='COST_AWARE_V1' and (type(max_candidates_per_pair) is not int or not 1<=max_candidates_per_pair<=24):return finish('INVALID_INPUT','INVALID_CANDIDATE_LIMIT','Cost-aware per-pair limit must be between 1 and 24')
        if objective_mode not in ('FEASIBILITY','MIN_TOTAL_DEMURRAGE'):return finish('INVALID_INPUT','INVALID_OBJECTIVE_MODE','Unknown objective mode')
        if inventory_constraint_mode not in ('NONE','EXACT_EVENT_BALANCE_V1') or (inventory_constraint_mode!='NONE' and objective_mode!='MIN_TOTAL_DEMURRAGE'):return finish('INVALID_INPUT','INVALID_INVENTORY_MODE','Exact inventory mode requires MIN_TOTAL_DEMURRAGE')
        result['objective_mode']=objective_mode
        result['inventory_constraint_mode']=inventory_constraint_mode
        if objective_mode=='MIN_TOTAL_DEMURRAGE':result.update(pricing_policy=pricing_policy,clock_policy=clock_policy)
        if type(max_inventory_reoptimizations) is not int or max_inventory_reoptimizations<0:return finish('INVALID_INPUT','INVALID_REOPTIMIZATION_LIMIT','Nonnegative integer reoptimization limit required')
        if candidate_generation_mode not in ('MANUAL_CANDIDATES','AUTO_ADAPTIVE_CANDIDATES'):return finish('INVALID_INPUT','INVALID_CANDIDATE_MODE','Unknown candidate generation mode')
        if candidate_generation_mode=='AUTO_ADAPTIVE_CANDIDATES' and (any(type(x) is not int or x<1 for x in (max_candidates_per_pair,max_total_candidates)) or type(max_expansion_rounds) is not int or max_expansion_rounds<0):return finish('INVALID_INPUT','INVALID_CANDIDATE_LIMIT','Invalid candidate limits')
        if snapshot.get('scenario_id')!='V5_3TANK' or snapshot.get('schema_version')!='DOCKTIMIZE_NORMALIZED_INPUT_V5':return finish('INVALID_INPUT','INVALID_V5_INPUT','Expected normalized V5 input')
        work=_verified_snapshot(snapshot,data_dir)
        option_cache={}
        def price_option(row):
            from .v5_settlement import price_v5_schedule
            key=canonical(row)
            if key not in option_cache:
                priced=price_v5_schedule(work,[row],as_of_at=as_of_at,settlement_records=settlement_records,pricing_policy=pricing_policy,clock_policy=clock_policy)
                option_cache[key]=priced['vessel_demurrage_breakdown'].get(row['call_id'],{'status':'SETTLEMENT_UNRESOLVED','issues':priced['issues']})
            return option_cache[key]
        def inventory_builder(model,variables,options,locked):
            from .v5_inventory_events import prepare_inventory_events
            from .v5_inventory_constraints import add_inventory_constraints
            from ..integrated_input.validator import number
            begin=timestamp(as_of_at);end=timestamp(projection_end_at) if projection_end_at else max([begin+timedelta(hours=72)]+[timestamp(row['work_end_at']) for row in options+locked])
            inventory=initial_inventory if initial_inventory is not None else {t['tank_id']:t['initial_inventory_bbl'] for t in work['tanks']}
            if initial_inventory is None and begin!=timestamp(work['fixed_process_flows']['inventory_as_of_at']):raise ValueError('Carry-in inventory required')
            if set(inventory)!={t['tank_id'] for t in work['tanks']}:raise ValueError('Carry-in inventory required for all tanks')
            for t in work['tanks']:
                if not number(t['min_inventory_bbl'])<=number(inventory[t['tank_id']])<=number(t['capacity_bbl']):raise ValueError('Invalid carry-in inventory: '+t['tank_id'])
            prepared=prepare_inventory_events(work,options+locked,start_at=as_of_at,end_at=end.isoformat(),carried_transfers=carried_transfers or (),commitments=tank_commitments or (),physical_commitments=locked)
            return add_inventory_constraints(model,variables,prepared,work['tanks'],initial_inventory=inventory,option_count=len(options))
        objective_args={'objective_mode':objective_mode,'option_pricer':price_option if objective_mode=='MIN_TOTAL_DEMURRAGE' else None,'inventory_builder':inventory_builder if inventory_constraint_mode!='NONE' else None}
        if tie_break_mode!='NONE':objective_args['tie_break_mode']=tie_break_mode
        if time.perf_counter()>=deadline:raise TimeoutError('Overall execution time budget exhausted during input validation')
        calls={c['call_id']:c for c in work['calls']}
        if not call_ids or len(set(call_ids))!=len(call_ids) or not set(call_ids)<=set(calls):return finish('INVALID_INPUT','INVALID_V5_INPUT','Invalid selected call IDs')
        result['unscheduled_calls']=sorted(calls)
        eligible={cid:eligible_berth_ids(calls[cid],work['berths'],work['tanks']) for cid in call_ids}
        blocked=[]
        if candidate_generation_mode=='AUTO_ADAPTIVE_CANDIDATES' and not user_v5(policy):
            for g in work['workbook_tables']['groups']['rows']:
                if g.get('rule_type')=='PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP':
                    members=set(tokens(g.get('berth_ids')))
                    for cid in call_ids:
                        for bid in sorted(members.intersection(eligible[cid])):blocked.append({'call_id':cid,'berth_id':bid,'code':'POLICY_UNRESOLVED','reason':'4-1/4-2 event and GRT exception policy unresolved'})
                        eligible[cid]=[bid for bid in eligible[cid] if bid not in members]
            result['rejection_reasons'].extend(blocked)
            blocked_berths={x['berth_id'] for x in blocked}
            if any(row.get('berth_id') in blocked_berths for row in physical_commitments):return finish('UNSUPPORTED_OPERATIONAL_POLICY','UNSUPPORTED_OPERATIONAL_POLICY','Protected commitment uses a berth with unresolved 4-1/4-2 policy')
            blocked_calls={x['call_id'] for x in blocked}
            if any(not eligible[cid] and cid in blocked_calls for cid in call_ids):return finish('UNSUPPORTED_OPERATIONAL_POLICY','UNSUPPORTED_OPERATIONAL_POLICY','No supported berth remains; 4-1/4-2 policy is unresolved')
        if not isinstance(policy,dict) or policy.get('cargo_duration_policy')!='VESSEL_BERTH_RATE_V1':return finish('UNSUPPORTED_OPERATIONAL_POLICY','UNSUPPORTED_OPERATIONAL_POLICY','Explicit VESSEL_BERTH_RATE_V1 and complete operating policy required')
        history_start=min((r['pob_at'] for r in physical_commitments),key=timestamp,default=None)
        policy,pilot_issue=prepare_pilotage_policy(work,call_ids,eligible,policy,pilotage_context,as_of_at,candidate_etbs,history_start)
        if time.perf_counter()>=deadline:raise TimeoutError('Overall execution time budget exhausted during policy preparation')
        if pilot_issue:
            result['issues'].append(pilot_issue);return finish('UNSUPPORTED_OPERATIONAL_POLICY')
        result['policy_fingerprint']=digest(canonical(policy).encode())
        try:validate_policy(policy,[b['berth_id'] for b in work['berths'] if b.get('active')=='Y'])
        except (ValueError,KeyError,TypeError) as exc:return finish('UNSUPPORTED_OPERATIONAL_POLICY','UNSUPPORTED_OPERATIONAL_POLICY',exc)
        used={b for ids in eligible.values() for b in ids}
        groups=work['workbook_tables']['groups']['rows'];retained=[]
        for group in groups:
            members=set(tokens(group.get('berth_ids')))
            if group.get('rule_type')=='PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP' and not user_v5(policy):
                if members & used:return finish('UNSUPPORTED_OPERATIONAL_POLICY','UNSUPPORTED_OPERATIONAL_POLICY','Source 4-1/4-2 event and GRT exception definitions remain unresolved',berth_ids=sorted(members & used))
                continue
            retained.append(group)
        work['workbook_tables']['groups']['rows']=retained
        if user_v5(policy):
            source_docks=[set(tokens(g.get('berth_ids'))) for g in retained if g.get('rule_type')=='PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP']
            if source_docks and any(x!=DOCK_PAIR for x in source_docks):
                return finish('UNSUPPORTED_OPERATIONAL_POLICY','DOCK_MAPPING_MISMATCH','Source dock group must map only to BERTH-07/08')
        if candidate_generation_mode=='AUTO_ADAPTIVE_CANDIDATES':
            from .v5_candidates import CandidatePool
            if not {r.get('call_id') for r in physical_commitments}<=set(call_ids):return finish('INVALID_INPUT','INVALID_COMMITMENT','Protected physical calls must be included in call_ids')
            protected_check=validate_result(work,physical_commitments,policy)
            if protected_check['status']!='VALID':return finish('INVALID_INPUT','INVALID_COMMITMENT',protected_check['issues'])
            pool=CandidatePool(work,{cid:calls[cid] for cid in call_ids},eligible,policy,as_of_at,commitments=physical_commitments,pair_limit=max_candidates_per_pair,total_limit=max_total_candidates,deadline=deadline)
            if frozen_candidate_domain is None:pool.initial()
            else:
                for cid in sorted(frozen_candidate_domain):
                    for bid in sorted(frozen_candidate_domain[cid]):
                        if cid not in calls or bid not in eligible.get(cid,[]):return finish('INVALID_INPUT','INVALID_CANDIDATE_DOMAIN','Frozen candidate pair is not eligible')
                        for at in frozen_candidate_domain[cid][bid]:pool.add(cid,bid,at,'FROZEN_VALIDATED_CANDIDATE',0)
                if pool.domain()!=frozen_candidate_domain:return finish('INVALID_INPUT','INVALID_CANDIDATE_DOMAIN','Frozen domain differs after physical validation')
            result['initial_candidate_count']=pool.count()
            for round_no in range(max_expansion_rounds+1):
                remaining=deadline-time.perf_counter()
                if remaining<=0:raise TimeoutError('Overall execution time budget exhausted')
                round_start=time.perf_counter()
                solve_budget=remaining/(max_improvement_rounds+1) if candidate_improvement_mode=='COST_AWARE_V1' else remaining
                physical=solve_berth_schedule(work,as_of_at=as_of_at,candidate_etbs={},pair_candidate_etbs=pool.domain(),policy=policy,time_limit_seconds=solve_budget,selected_call_ids=call_ids,allowed_berths_by_call=eligible,commitments=physical_commitments,forbidden_schedules=initial_forbidden_schedules,conflict_filter='SAFE_ENVELOPE_V1',_v5_context=_V5_VALIDATED_CONTEXT,**objective_args)
                result['generation_rounds'].append({'round':round_no,'candidate_count':pool.count(),'solver_outcome':physical['status'],'cp_sat_seconds':physical.get('cp_sat_seconds'),'elapsed_seconds':time.perf_counter()-started,'round_seconds':time.perf_counter()-round_start,'expansion_reason':'INITIAL' if round_no==0 else 'CANDIDATE_SHORTAGE_OR_FINITE_CONFLICT'})
                result['solver_attempt_count']=len(result['generation_rounds'])
                if physical['status'] not in ('INFEASIBLE',):break
                if frozen_candidate_domain is not None:result['stopping_reason']='CANDIDATE_DOMAIN_EXHAUSTED';break
                if round_no==max_expansion_rounds:result['stopping_reason']='EXPANSION_ROUND_LIMIT';break
                if pool.count()>=max_total_candidates:result['stopping_reason']='TOTAL_CANDIDATE_LIMIT';break
                affected=pool.affected_calls()
                if pool.expand(round_no+1,affected)==0:result['stopping_reason']='CANDIDATE_DOMAIN_EXHAUSTED';break
            if physical['status']=='INFEASIBLE' and (blocked or any(x['code']=='POLICY_UNRESOLVED' for x in pool.rejected)):
                result['solver_status']=physical['status'];result['physical_solver_result']=physical
                return finish('UNSUPPORTED_OPERATIONAL_POLICY','UNSUPPORTED_OPERATIONAL_POLICY','Some potentially relevant candidates have unresolved policy')
        else:
            manual_domain={cid:{bid:sorted({timestamp(v).isoformat() for v in ([calls[cid]['fix_etb_at']] if calls[cid]['fixed_etb_is_hard'] else candidate_etbs.get(cid,[]))}) for bid in sorted(eligible[cid])} for cid in sorted(call_ids)}
            manual_domain_fingerprint=digest(canonical({'as_of_at':as_of_at,'call_ids':sorted(call_ids),'pair_candidate_etbs':manual_domain,'physical_commitments':list(physical_commitments)}).encode())
            result['candidates_by_call_and_berth']=manual_domain
            result['generation_reasons']={cid:{bid:[{'etb_at':t,'reasons':['HARD_FIX_ETB' if calls[cid]['fixed_etb_is_hard'] else 'MANUAL_SUPPLIED'],'round':0} for t in times] for bid,times in pairs.items()} for cid,pairs in manual_domain.items()}
            result['initial_candidate_count']=result['final_candidate_count']=sum(len(times) for pairs in manual_domain.values() for times in pairs.values())
            remaining=deadline-time.perf_counter()
            if remaining<=0:raise TimeoutError('Overall execution time budget exhausted')
            physical=solve_berth_schedule(work,as_of_at=as_of_at,candidate_etbs=candidate_etbs,policy=policy,time_limit_seconds=remaining,selected_call_ids=call_ids,allowed_berths_by_call=eligible,commitments=physical_commitments,forbidden_schedules=initial_forbidden_schedules,_v5_context=_V5_VALIDATED_CONTEXT,**objective_args)
            result['solver_attempt_count']=1
        result['solver_status']=physical.get('solver_status',physical['status']);result['candidate_domain_fingerprint']=physical.get('candidate_domain_fingerprint',result['candidate_domain_fingerprint']);result['physical_solver_result']=physical
        result['physical_solver_status']=result['solver_status']
        if physical['status']!='BERTH_SUBSYSTEM_FEASIBLE':
            result['issues']=physical.get('issues',[])
            if any(i.get('code')=='NO_FEASIBLE_GENERATED_OPTION' for i in result['issues']):return finish('NO_FEASIBLE_GENERATED_OPTION')
            if physical['status']=='INFEASIBLE' and all(calls[cid].get('fixed_etb_is_hard') is True for cid in call_ids):
                return finish('INFEASIBLE','FIX_PRECHECK_FAILED','Fixed calls conflict within the supplied candidate domain',call_ids=sorted(call_ids))
            return finish(physical['status'])
        from .v5_reoptimization import search
        def evaluate(rows):
            independent=validate_result(work,rows,policy)
            evaluation={'physical_validation':independent,'inventory_validation':'NOT_EVALUATED','inventory_result':None,'selected_berths':{},'selected_etbs':{},'issues':[], 'tank_limits':{t['tank_id']:{'min_inventory_bbl':t['min_inventory_bbl'],'capacity_bbl':t['capacity_bbl']} for t in work['tanks']}}
            if independent.get('status')!='VALID':
                evaluation.update(status='PHYSICAL_VALIDATION_FAILED',issues=independent.get('issues',[]));return evaluation
            for row in rows:
                if row['berth_id'] not in eligible[row['call_id']]:
                    evaluation.update(status='PHYSICAL_VALIDATION_FAILED',issues=[{'code':'MISSING_TANK_CONNECTION','call_id':row['call_id'],'berth_id':row['berth_id']}]);return evaluation
            evaluation['selected_berths']={r['call_id']:r['berth_id'] for r in rows};evaluation['selected_etbs']={r['call_id']:r['etb_at'] for r in rows}
            if {r['call_id'] for r in rows}!=set(call_ids):
                evaluation.update(status='INTEGRATION_VALIDATION_FAILED',issues=[{'code':'NO_FEASIBLE_GENERATED_OPTION','reason':'Not every selected call was scheduled'}]);return evaluation
            end=timestamp(projection_end_at) if projection_end_at else max([timestamp(as_of_at)+timedelta(hours=72)]+[timestamp(r['work_end_at']) for r in rows])
            if end<max(timestamp(r['work_end_at']) for r in rows):
                evaluation.update(status='INTEGRATION_VALIDATION_FAILED',issues=[{'code':'INVALID_FLOW_COVERAGE','reason':'Projection must cover all vessel transfers'}]);return evaluation
            inv=validate_v5_candidate(work,rows,as_of_at=as_of_at,projection_end_at=end.isoformat(),initial_inventory=initial_inventory,carried_transfers=carried_transfers,tank_commitments=tank_commitments,same_tank_policy='ALLOW',physical_commitments=physical_commitments)
            evaluation['inventory_result']=inv;evaluation['inventory_validation']=inv['status']
            if inv['status']!='INVENTORY_VALID':
                evaluation.update(status='INVENTORY_VALIDATION_FAILED',issues=inv.get('issues',[]));return evaluation
            assignments={x['call_id']:x for x in inv['tank_result']['assignments']};definitions={x['call_id']:x for x in inv['tank_result']['transfer_definitions']}
            active_rows=[r for r in rows if timestamp(r['work_end_at'])>timestamp(as_of_at)]
            if set(assignments)!={r['call_id'] for r in active_rows} or set(definitions)!={r['call_id'] for r in active_rows}:
                evaluation.update(status='INTEGRATION_VALIDATION_FAILED',issues=[{'code':'INTEGRATION_VALIDATION_FAILED','reason':'Physical and inventory call sets differ'}]);return evaluation
            for row in active_rows:
                x=definitions[row['call_id']]
                if x['berth_id']!=row['berth_id'] or timestamp(x['etb_at'])!=timestamp(row['etb_at']) or timestamp(x['start_at'])!=timestamp(row['work_intervals'][0][0]) or timestamp(x['end_at'])!=timestamp(row['work_intervals'][-1][1]):
                    evaluation.update(status='INTEGRATION_VALIDATION_FAILED',issues=[{'code':'INTEGRATION_VALIDATION_FAILED','reason':'Physical and inventory transfer definitions differ','call_id':row['call_id']}]);return evaluation
            evaluation['status']='PHYSICAL_AND_INVENTORY_VALID';return evaluation
        def solve_alternative(forbidden,remaining):
            kwargs={'as_of_at':as_of_at,'policy':policy,'time_limit_seconds':remaining,'selected_call_ids':call_ids,'allowed_berths_by_call':eligible,'commitments':physical_commitments,'forbidden_schedules':forbidden,'_v5_context':_V5_VALIDATED_CONTEXT}
            if candidate_generation_mode=='AUTO_ADAPTIVE_CANDIDATES':kwargs.update(candidate_etbs={},pair_candidate_etbs=pool.domain(),conflict_filter='SAFE_ENVELOPE_V1')
            else:kwargs['candidate_etbs']=candidate_etbs
            return solve_berth_schedule(work,**kwargs)
        if objective_mode=='MIN_TOTAL_DEMURRAGE':
            if candidate_improvement_mode=='COST_AWARE_V1' and time.perf_counter()>=deadline:return finish('TIME_LIMIT_NO_INCUMBENT','TIME_BUDGET_EXHAUSTED','Budget exhausted before baseline validation')
            evaluation=evaluate(physical['schedule'])
            if inventory_constraint_mode!='NONE' and evaluation['status']!='PHYSICAL_AND_INVENTORY_VALID':
                result.update(physical_validation=evaluation['physical_validation'],inventory_validation=evaluation['inventory_validation'],issues=evaluation['issues'],selected_schedule=physical['schedule'],optimality_scope='NOT_VALIDATED')
                return finish('INVENTORY_MODEL_VALIDATION_MISMATCH')
            if inventory_constraint_mode!='NONE':
                from .v5_settlement import price_v5_schedule
                check_price=price_v5_schedule(work,physical['schedule'],as_of_at=as_of_at,settlement_records=settlement_records,pricing_policy=pricing_policy,clock_policy=clock_policy)
                if check_price['status']!='SETTLED' or check_price['total_demurrage_cents']!=physical['total_projected_demurrage_cents']:
                    result.update(issues=check_price['issues'] or [{'code':'SETTLEMENT_MODEL_MISMATCH','reason':'Selected schedule cost differs from modeled objective'}],optimality_scope='NOT_VALIDATED')
                    return finish('INTEGRATION_VALIDATION_FAILED')
            if candidate_improvement_mode=='COST_AWARE_V1' and time.perf_counter()>=deadline:return finish('TIME_LIMIT_NO_INCUMBENT','TIME_BUDGET_EXHAUSTED','Budget exhausted during baseline validation')
            if candidate_improvement_mode=='COST_AWARE_V1':
                def domain_hash():return digest(canonical({'as_of_at':as_of_at,'call_ids':sorted(call_ids),'pair_candidate_etbs':pool.domain(),'physical_commitments':list(physical_commitments)}).encode())
                if tie_break_mode!='NONE':
                    from ..physical_scheduling.v5_stable_choice import schedule_key as schedule_key_for_ties
                best=physical;best_eval=evaluation;best_price=check_price;best_round=0;best_domain=domain_hash();initial_cost=physical['total_projected_demurrage_cents'];final_domain=best_domain
                history=[{'round':0,'candidate_count':pool.count(),'physical_option_count':physical['option_count'],'solver_status':physical['solver_status'],'validated_cost_cents':initial_cost,'candidate_domain_fingerprint':best_domain,'elapsed_seconds':time.perf_counter()-started}]
                stop='IMPROVEMENT_ROUND_LIMIT';last_status=physical['solver_status'];final_proven=last_status=='OPTIMAL';final_solver_result=physical
                for round_no in range(1,max_improvement_rounds+1):
                    try:
                        costs={cid:v['demurrage_cents'] for cid,v in best_price['vessel_demurrage_breakdown'].items()}
                        added=pool.expand_cost(round_no,best['schedule'],costs)
                        if not added:stop='NO_NEW_CANDIDATES';break
                        final_domain=domain_hash();final_proven=False;last_status='NOT_SOLVED';remaining=deadline-time.perf_counter()
                        if remaining<=0:stop='TIME_BUDGET_EXHAUSTED';break
                        candidate=solve_berth_schedule(work,as_of_at=as_of_at,candidate_etbs={},pair_candidate_etbs=pool.domain(),policy=policy,time_limit_seconds=remaining/(max_improvement_rounds-round_no+1),selected_call_ids=call_ids,allowed_berths_by_call=eligible,commitments=physical_commitments,forbidden_schedules=initial_forbidden_schedules,conflict_filter='SAFE_ENVELOPE_V1',_v5_context=_V5_VALIDATED_CONTEXT,**objective_args)
                        result['solver_attempt_count']+=1;last_status=candidate.get('solver_status',candidate['status']);final_solver_result=candidate
                        entry={'round':round_no,'candidate_count':pool.count(),'physical_option_count':candidate.get('option_count'),'solver_status':last_status,'validated_cost_cents':None,'candidate_domain_fingerprint':final_domain,'elapsed_seconds':time.perf_counter()-started,'new_candidate_reasons':pool.reasons()}
                        history.append(entry)
                        if candidate['status']!='BERTH_SUBSYSTEM_FEASIBLE':stop=candidate['status'];break
                        if time.perf_counter()>=deadline:stop='TIME_BUDGET_EXHAUSTED';break
                        checked=evaluate(candidate['schedule']);priced=price_v5_schedule(work,candidate['schedule'],as_of_at=as_of_at,settlement_records=settlement_records,pricing_policy=pricing_policy,clock_policy=clock_policy)
                        if time.perf_counter()>=deadline:stop='TIME_BUDGET_EXHAUSTED';break
                        if checked['status']!='PHYSICAL_AND_INVENTORY_VALID' or priced['status']!='SETTLED' or priced['total_demurrage_cents']!=candidate['total_projected_demurrage_cents']:stop='INDEPENDENT_VALIDATION_FAILED';break
                        entry['validated_cost_cents']=priced['total_demurrage_cents'];final_proven=last_status=='OPTIMAL'
                        if (priced['total_demurrage_cents']<best['total_projected_demurrage_cents'] or
                            (tie_break_mode!='NONE' and priced['total_demurrage_cents']==best['total_projected_demurrage_cents'] and
                             candidate.get('schedule_tiebreak',{}).get('status')=='PROVEN_CANONICAL_SELECTION' and
                             (best.get('schedule_tiebreak',{}).get('status')!='PROVEN_CANONICAL_SELECTION' or
                              schedule_key_for_ties(candidate['schedule'])<schedule_key_for_ties(best['schedule'])))):
                            best,best_eval,best_price,best_round,best_domain=candidate,checked,priced,round_no,final_domain
                    except TimeoutError:
                        if domain_hash()!=final_domain or final_domain!=best_domain:final_proven=False
                        stop='TIME_BUDGET_EXHAUSTED';break
                    except Exception as exc:
                        if domain_hash()!=best_domain:final_proven=False
                        stop='IMPROVEMENT_ERROR';history.append({'round':round_no,'error':type(exc).__name__+': '+str(exc),'elapsed_seconds':time.perf_counter()-started});break
                physical,evaluation,check_price=best,best_eval,best_price
                result.update(initial_total_demurrage_cents=initial_cost,best_total_demurrage_cents=physical['total_projected_demurrage_cents'],best_incumbent_round=best_round,best_incumbent_domain_fingerprint=best_domain,candidate_expansion_rounds=len(history)-1,cost_improvement_history=history,final_solver_status=last_status,final_solver_result=final_solver_result,final_domain_optimality_proven=final_proven,stopping_reason=stop)
                result['physical_solver_result']=physical;result['solver_status']=physical['solver_status']
            if tie_break_mode!='NONE':result['schedule_tiebreak']=physical.get('schedule_tiebreak',{'status':'NOT_REPORTED'})
            result.update(optimization_status=physical['solver_status'],selected_schedule=physical['schedule'],
                total_projected_demurrage_cents=physical['total_projected_demurrage_cents'],
                option_cost_breakdown=physical['option_cost_breakdown'],best_objective_bound_cents=physical['best_objective_bound_cents'],
                optimality_gap=physical['optimality_gap'],physical_option_count=physical['option_count'],
                candidate_count=result['final_candidate_count'] if pool is None else pool.count(),
                optimality_scope=physical['solver_objective_scope'],simulation_only=all(x['pricing'].get('simulation_only',False) for x in physical['option_cost_breakdown']+physical['committed_cost_breakdown']),
                physical_validation=evaluation['physical_validation'],inventory_validation=evaluation['inventory_validation'],inventory_result=evaluation['inventory_result'],
                selected_berths=evaluation['selected_berths'],selected_etbs=evaluation['selected_etbs'],issues=evaluation['issues'],
                scheduled_calls=sorted(evaluation['selected_berths']),unscheduled_calls=sorted(set(call_ids)-set(evaluation['selected_berths'])),
                combined_validation='PHYSICAL_AND_INVENTORY_VALID' if evaluation['status']=='PHYSICAL_AND_INVENTORY_VALID' else 'NOT_VALIDATED')
            if inventory_constraint_mode!='NONE':
                result.update({key:physical[key] for key in ('inventory_event_count','inventory_constraints_count','throughput_constraints_count')})
                result['optimality_scope']='INTEGRATED_FINITE_DOMAIN_OPTIMAL' if physical['solver_status']=='OPTIMAL' else 'INTEGRATED_FINITE_DOMAIN_INCUMBENT'
                if candidate_improvement_mode=='COST_AWARE_V1' and not result['final_domain_optimality_proven']:result['optimality_scope']='BEST_VALIDATED_INCUMBENT_EXPANDED_DOMAIN_UNPROVEN'
                result['settlement_validation']='SETTLED'
            if candidate_improvement_mode=='COST_AWARE_V1':
                actual_domain=domain_hash()
                last=next((h for h in reversed(history) if h.get('candidate_domain_fingerprint')==actual_domain),None)
                trusted=bool(last and last.get('validated_cost_cents') is not None and final_solver_result.get('status')=='BERTH_SUBSYSTEM_FEASIBLE')
                final_status=last['solver_status'] if last else 'NOT_SOLVED'
                bound=final_solver_result.get('best_objective_bound_cents') if trusted else None
                if bound is not None and (not math.isfinite(bound) or bound>physical['total_projected_demurrage_cents']):bound=None
                cost=physical['total_projected_demurrage_cents']
                gap=(max(0.0,cost-bound)/cost if cost else 0.0) if bound is not None else None
                proven=bool(trusted and final_status=='OPTIMAL' and final_proven and last['validated_cost_cents']==cost)
                result.update(solver_status=final_status if trusted else ('NOT_SOLVED' if last is None else 'UNVALIDATED'),
                    optimization_status='OPTIMAL' if proven else 'FEASIBLE',best_objective_bound_cents=bound,optimality_gap=gap,
                    final_solver_status=final_status,final_domain_optimality_proven=proven,
                    optimality_scope='INTEGRATED_FINITE_DOMAIN_OPTIMAL' if proven else 'BEST_VALIDATED_INCUMBENT_EXPANDED_DOMAIN_UNPROVEN')
                result['physical_solver_status']=result['solver_status']
                if last is None:result['final_solver_result']=None
                result['best_validated_incumbent']={'selected_schedule':physical['schedule'],'total_demurrage_cents':cost,'round':best_round,'candidate_domain_fingerprint':best_domain,'solver_status':physical['solver_status'],'physical_validation':evaluation['physical_validation'],'inventory_validation':evaluation['inventory_validation'],'settlement_validation':check_price['status']}
                result['final_candidate_domain']={'candidate_domain_fingerprint':actual_domain,'candidate_count':pool.count(),'physical_option_count':final_solver_result.get('option_count') if last else None,'solver_status':final_status,'optimality_proven':proven,'best_objective_bound_cents':bound,'optimality_gap':gap,'stopping_reason':stop}
            return finish(evaluation['status'])
        search_result=search(physical,evaluate=evaluate,solve_alternative=solve_alternative,deadline=deadline,max_reoptimizations=max_inventory_reoptimizations,initial_forbidden_schedules=initial_forbidden_schedules)
        result['inventory_reoptimization_solver_calls']=search_result['solver_calls']-1;result['solver_attempt_count']=(len(result['generation_rounds']) if candidate_generation_mode=='AUTO_ADAPTIVE_CANDIDATES' else 1)+result['inventory_reoptimization_solver_calls']
        result['reoptimization_attempts']=search_result['attempts'];result['forbidden_schedule_count']=len(search_result['forbidden_schedules']);result['inventory_violations']=[{'attempt':a['attempt'],**issue} for a in search_result['attempts'] for issue in a['inventory_violations']]
        result['stopping_reason']=search_result['stopping_reason'];result['physical_solver_result']=search_result['physical'];result['last_reoptimization_solver_result']=search_result['last_search_result']
        if search_result['stopping_reason']=='CANDIDATE_DOMAIN_EXHAUSTED' and (blocked or (pool is not None and any(x.get('code')=='POLICY_UNRESOLVED' for x in pool.rejected))):
            result['stopping_reason']='CANDIDATE_DOMAIN_EXHAUSTED_POLICY_PENDING'
        if search_result['physical']:result['physical_solver_status']=search_result['physical'].get('solver_status',search_result['physical']['status']);result['solver_status']=result['physical_solver_status']
        if search_result['last_search_result'] and search_result['last_search_result'].get('status')!='BERTH_SUBSYSTEM_FEASIBLE':
            result['physical_solver_status']=search_result['last_search_result'].get('solver_status',search_result['last_search_result']['status']);result['solver_status']=result['physical_solver_status']
        evaluation=search_result['evaluation']
        if evaluation:
            result['physical_validation']=evaluation['physical_validation'];result['inventory_validation']=evaluation['inventory_validation'];result['inventory_validation_status']=evaluation['inventory_validation'];result['inventory_result']=evaluation['inventory_result'];result['final_inventory_result']=evaluation['inventory_result'];result['selected_berths']=evaluation['selected_berths'];result['selected_etbs']=evaluation['selected_etbs'];result['issues']=evaluation['issues']
            result['scheduled_calls']=sorted(evaluation['selected_berths']);result['unscheduled_calls']=sorted(set(calls)-set(result['scheduled_calls']))
        if search_result['status']=='PHYSICAL_AND_INVENTORY_VALID':result['combined_validation']='PHYSICAL_AND_INVENTORY_VALID'
        if search_result['stopping_reason']=='DUPLICATE_SCHEDULE_DETECTED':result['issues'].append({'code':'DUPLICATE_SCHEDULE_DETECTED','reason':'Solver returned an excluded schedule'})
        if search_result['status']=='TIME_LIMIT_NO_INCUMBENT':result['issues'].append({'code':'TIME_BUDGET_EXHAUSTED','reason':'Shared execution-time budget exhausted during reoptimization'})
        if result['stopping_reason']=='CANDIDATE_DOMAIN_EXHAUSTED_POLICY_PENDING':result['issues'].append({'code':'UNSUPPORTED_OPERATIONAL_POLICY','reason':'Unresolved options remain outside the searched supported domain'})
        return finish(search_result['status'])
    except TimeoutError as exc:return finish('TIME_LIMIT_NO_INCUMBENT','TIME_BUDGET_EXHAUSTED',exc)
    except (OSError,ValueError,TypeError,KeyError,ImportError) as exc:return finish('INVALID_INPUT','INVALID_V5_INPUT',exc)
