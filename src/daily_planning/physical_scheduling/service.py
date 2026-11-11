"""Finite-candidate CP-SAT berth feasibility, isolated from production planning."""
from datetime import timedelta
import math
import time
from ..integrated_input.validator import timestamp
from ..integrated_input.provenance import canonical,digest
from .policy import validate_policy,event_definition_issues
from .phases import replay
from .compatibility import compatibility
from .validator import validate_result,pair_conflicts,restriction_conflicts

MODEL_VERSION='V3_FINITE_BERTH_PHASES_V1'
_V5_VALIDATED_CONTEXT=object()


def solve_berth_schedule(snapshot,*,as_of_at,candidate_etbs,policy,commitments=(),time_limit_seconds=30,forbidden_schedules=(),completed_rows=(),conflict_filter='NONE',phase_release_policy='ALL_OPTIONS_V1',candidate_visitor=None,search_heuristic='NONE',selected_call_ids=None,allowed_berths_by_call=None,pair_candidate_etbs=None,_v5_context=None):
    started=time.perf_counter()
    def failure(status,code,reason,**details):
        return {'status':status,'issues':[dict(code=code,reason=str(reason),**details)],
                'model_version':MODEL_VERSION,'operational_approval':False}
    try:
        begin=timestamp(as_of_at);end=begin+timedelta(hours=72)
        if isinstance(time_limit_seconds,bool) or not math.isfinite(time_limit_seconds) or time_limit_seconds<=0:raise ValueError('Invalid solver budget')
        if search_heuristic not in ('NONE','CRUDE_INBOUND_FIRST_V1'):raise ValueError('Unknown search heuristic')
        if conflict_filter not in ('NONE','SAFE_ENVELOPE_V1'):raise ValueError('Unknown conflict filter')
        if phase_release_policy not in ('ALL_OPTIONS_V1','LOCKED_ONLY_V1'):raise ValueError('Unknown phase release policy')
        deadline=started+float(time_limit_seconds)
        def check_deadline():
            if time.perf_counter()>=deadline:raise TimeoutError('Berth candidate generation or model construction exceeded budget')
        schema=snapshot.get('schema_version')
        if schema not in ('DOCKTIMIZE_NORMALIZED_INPUT_V3','DOCKTIMIZE_NORMALIZED_INPUT_V5'):raise ValueError('Normalized V3 or V5 snapshot required')
        if schema=='DOCKTIMIZE_NORMALIZED_INPUT_V5' and _v5_context is not _V5_VALIDATED_CONTEXT:raise ValueError('V5 requires the verified V5 integration path')
        if schema=='DOCKTIMIZE_NORMALIZED_INPUT_V5' and ('source_input' not in snapshot or not {'groups','restrictions'}<=set(snapshot.get('workbook_tables',{}))):raise ValueError('V5 requires validated source and physical restriction tables')
        if 'source_input' in snapshot:
            from ..integrated_input.validator import validate
            if schema=='DOCKTIMIZE_NORMALIZED_INPUT_V5':
                from ..integrated_input.v5 import validate_v5
                preflight=validate_v5(snapshot)
                if not preflight['data_preflight_pass']:return failure('INVALID_INPUT','INVALID_V5_INPUT',preflight['errors'])
            elif snapshot['fixed_process_flows'].get('extension_provenance'):
                from ..integrated_input.flow_extension import validate_extended_snapshot,check_extension_available
                check_extension_available(snapshot,as_of_at=as_of_at,projection_end_at=end.isoformat())
                preflight=validate_extended_snapshot(snapshot)
            else:preflight=validate(snapshot)
            if not preflight['data_preflight_pass']:return failure('INVALID_INPUT','V3_DATA_PREFLIGHT_FAILED',preflight['issues'])
        calls={c['call_id']:c for c in snapshot['calls']};berths={b['berth_id']:b for b in snapshot['berths']}
        if len(calls)!=len(snapshot['calls']) or len(berths)!=len(snapshot['berths']):raise ValueError('Duplicate source identity')
        if selected_call_ids is not None:
            selected=set(selected_call_ids)
            if not selected or len(selected)!=len(selected_call_ids) or not selected<=set(calls):raise ValueError('Invalid selected call IDs')
            calls={cid:c for cid,c in calls.items() if cid in selected}
        if schema=='DOCKTIMIZE_NORMALIZED_INPUT_V5':
            from ..integrated_input.v5 import eligible_berth_ids
            verified={cid:eligible_berth_ids(call,snapshot['berths'],snapshot['tanks']) for cid,call in calls.items()}
            if allowed_berths_by_call is None:allowed_berths_by_call=verified
            else:allowed_berths_by_call={cid:[bid for bid in verified[cid] if bid in allowed_berths_by_call.get(cid,())] for cid in calls}
        active=[b for b in berths.values() if b.get('active')=='Y']
        validate_policy(policy,[b['berth_id'] for b in active])
        definition_issues=event_definition_issues(snapshot,policy)
        if definition_issues and policy.get('event_definition_resolution')!='EXPLICIT_PHASED_SYNTHETIC_RESOLUTION':
            return dict(failure('UNSUPPORTED_OPERATIONAL_POLICY','EVENT_DEFINITION_UNRESOLVED','4142 ETU/phase policy must be resolved before search'),event_definition_issues=definition_issues)
        for block in snapshot['workbook_tables']['restrictions']['rows']:
            if block.get('berth_id') not in berths or block.get('restriction_scope') not in ('WORK','BOTH') or timestamp(block['start_local'])>=timestamp(block['end_local']):raise ValueError('Invalid source berth restriction')
        for g in snapshot['workbook_tables']['groups']['rows']:
            if g['rule_type'] not in ('SAME_CARGO_NOT_CONCURRENT','PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP'):raise ValueError('Unsupported group resource rule')
            if g['rule_type']=='PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP' and policy['dock_4142_version']=='DISABLED':raise ValueError('4142 policy version must be selected')
        locked=list(commitments);locked_ids={r['call_id'] for r in locked}
        if len(locked_ids)!=len(locked):raise ValueError('Duplicate commitments')
        from .completed import completed_call_ids
        completed=completed_call_ids(snapshot,completed_rows,policy,as_of_at=as_of_at,locked_ids=locked_ids)
        check=validate_result(snapshot,locked,policy)
        if check['status']!='VALID':return failure('INVALID_INPUT','COMMITMENT_VALIDATION_FAILED',check['issues'])
        outside=[];options=[];groups=[];rejected=[]
        for cid,call in sorted(calls.items()):
            check_deadline()
            if cid in locked_ids or cid in completed:continue
            eligible_time=timestamp(call['fix_etb_at']) if call['fixed_etb_is_hard'] else timestamp(call['eta_at'])
            if eligible_time>=end:outside.append(cid);continue
            if call['fixed_etb_is_hard'] and eligible_time<begin:return failure('INFEASIBLE','FIX_PRECHECK_FAILED','Past FIX must have an explicit carried commitment',call_id=cid)
            times=[call['fix_etb_at']] if call['fixed_etb_is_hard'] else candidate_etbs.get(cid,[])
            times=sorted({timestamp(value) for value in times})
            selected=[]
            for berth in active:
                if allowed_berths_by_call is not None and berth['berth_id'] not in allowed_berths_by_call.get(cid,()):continue
                if compatibility(call,berth):continue
                if pair_candidate_etbs is None:berth_times=times
                else:
                    pair_times=sorted({timestamp(value) for value in pair_candidate_etbs.get(cid,{}).get(berth['berth_id'],[])})
                    berth_times=[at for at in pair_times if at in times] if call['fixed_etb_is_hard'] else pair_times
                for at in berth_times:
                    check_deadline()
                    if not begin<=at<end:continue
                    try:
                        row=replay(call,berth,at.isoformat(),policy,restrictions=snapshot['workbook_tables']['restrictions']['rows'])
                        if timestamp(row['pob_at'])<begin:raise ValueError('New physical work would start before run boundary')
                        if validate_result(snapshot,[row],policy)['status']!='VALID':raise ValueError('Independent single-option phase/resource validation failed')
                    except ValueError as exc:
                        rejected.append({'call_id':cid,'berth_id':berth['berth_id'],'etb_at':at.isoformat(),'reason':str(exc)})
                        continue
                    selected.append(len(options));options.append(row)
            if not selected:return dict(failure('INFEASIBLE','FIX_PRECHECK_FAILED' if call['fixed_etb_is_hard'] else 'NO_FEASIBLE_GENERATED_OPTION','No validated berth/start candidate',call_id=cid),rejected_options=rejected,
                                         scope='Only supplied candidate ETBs; no continuous-time infeasibility claim')
            groups.append(selected)
        # ETB and hose connection are independent decisions. Add only explicit
        # resource-release anchors derived from immutable candidate replays.
        base_options=list(options)
        from .release_index import ReleaseEventIndex
        releases=ReleaseEventIndex(base_options,locked,policy,phase_release_policy)
        for base in base_options:
            check_deadline()
            for release in releases.later_releases(base):
                check_deadline()
                try:
                    row=replay(calls[base['call_id']],berths[base['berth_id']],base['etb_at'],policy,
                               restrictions=snapshot['workbook_tables']['restrictions']['rows'],hose_connection_start_at=release)
                    if validate_result(snapshot,[row],policy)['status']=='VALID':options.append(row)
                except ValueError:continue
        unique={canonical(row):row for row in options}
        options=[row for row in unique.values() if not any(pair_conflicts(row,r,snapshot,policy) for r in locked)]
        groups=[]
        for cid,call in sorted(calls.items()):
            if cid in locked_ids or cid in completed or cid in outside:continue
            indices=[i for i,row in enumerate(options) if row['call_id']==cid]
            if not indices:return failure('INFEASIBLE','FIX_PRECHECK_FAILED' if call['fixed_etb_is_hard'] else 'NO_FEASIBLE_GENERATED_OPTION','All generated options conflict with protected work',call_id=cid)
            groups.append(indices)
        from ortools.sat.python import cp_model
        m=cp_model.CpModel();x=[m.NewBoolVar('choice_'+str(i)) for i in range(len(options))]
        for indices in groups:m.AddExactlyOne(x[i] for i in indices)
        # Exact no-goods only: forbid a demonstrated complete selection, not
        # a berth/time choice shared by other potentially feasible schedules.
        option_index={canonical(row):i for i,row in enumerate(options)}
        locked_keys={canonical(row) for row in locked}
        for schedule in forbidden_schedules:
            if not isinstance(schedule,(list,tuple)):raise ValueError('Invalid forbidden schedule')
            keys=[canonical(row) for row in schedule]
            if len(keys)!=len(set(keys)):raise ValueError('Duplicate forbidden assignment')
            keyset=set(keys)
            if not locked_keys<=keyset or len(keys)!=len(groups)+len(locked):continue
            selected=keyset-locked_keys
            if any(key not in option_index for key in selected):continue
            if not selected:return failure('INFEASIBLE','EXCLUDED_COMPLETE_ASSIGNMENT','Protected complete assignment has been excluded')
            indices=[option_index[key] for key in selected]
            if any(sum(index in group for index in indices)!=1 for group in groups):continue
            m.Add(sum(x[index] for index in indices)<=len(indices)-1)
        conflicts=0;pair_checks=0
        if conflict_filter=='SAFE_ENVELOPE_V1':
            from .conflict_index import iter_potential_pairs
            pairs=iter_potential_pairs(snapshot,policy,options,check_deadline=check_deadline)
        else:
            pairs=((i,j) for i in range(len(options)) for j in range(i+1,len(options)) if options[i]['call_id']!=options[j]['call_id'])
        for i,j in pairs:
            if pair_checks%128==0:check_deadline()
            pair_checks+=1
            if pair_conflicts(options[i],options[j],snapshot,policy):m.AddBoolOr([x[i].Not(),x[j].Not()]);conflicts+=1
        tug_intervals=[];demands=[]
        def offset(value):return int((timestamp(value)-begin).total_seconds())
        for i,row in enumerate(options+locked if policy.get('tug_capacity') is not None else []):
            for phase,(a,b) in enumerate([(row['berthing_start_at'],row['etb_at']),(row['unberthing_start_at'],row['etu_at'])]):
                a,b=offset(a),offset(b)
                demand=row['tug_units']['berthing' if phase==0 else 'unberthing']
                if b<=a or not demand:continue
                interval=m.NewOptionalIntervalVar(a,b-a,b,x[i],f'tug_{i}_{phase}') if i<len(options) else m.NewIntervalVar(a,b-a,b,f'locked_tug_{i}_{phase}')
                tug_intervals.append(interval);demands.append(demand)
        if tug_intervals:m.AddCumulative(tug_intervals,demands,policy['tug_capacity'])
        if search_heuristic=='CRUDE_INBOUND_FIRST_V1':
            weighted=[]
            for i,row in enumerate(options):
                call=calls[row['call_id']]
                if call.get('cargo_group')!='CRUDE':continue
                offset=int((timestamp(row['etb_at'])-begin).total_seconds())
                if call.get('direction')=='DISCHARGE':weight=offset
                elif call.get('direction')=='LOAD':weight=72*3600-offset
                else:continue
                weighted.append(weight*x[i])
            m.Minimize(sum(weighted))
        else:m.Minimize(0)
        solver=cp_model.CpSolver();solver.parameters.num_search_workers=1;solver.parameters.random_seed=42
        emitted=0
        while True:
            check_deadline()
            solver.parameters.max_time_in_seconds=max(0.000001,deadline-time.perf_counter())
            result=solver.Solve(m)
            if result not in (cp_model.FEASIBLE,cp_model.OPTIMAL):
                if candidate_visitor is not None and result==cp_model.INFEASIBLE:
                    return {'status':'ENUMERATION_EXHAUSTED','emitted':emitted,'operational_approval':False}
                return failure('INFEASIBLE' if result==cp_model.INFEASIBLE else 'TIME_LIMIT_NO_INCUMBENT','NO_VALIDATED_CANDIDATE',solver.StatusName(result),scope='Generated options only',**({'solver_status':solver.StatusName(result),'cp_sat_seconds':solver.WallTime()} if schema=='DOCKTIMIZE_NORMALIZED_INPUT_V5' else {}))
            selected=[i for i in range(len(options)) if solver.Value(x[i])]
            rows=locked+[options[i] for i in selected]
            rows.sort(key=lambda row:(timestamp(row['etb_at']),row['call_id']))
            independent=validate_result(snapshot,rows,policy)
            if independent['status']!='VALID':return failure('VALIDATION_FAILED','INDEPENDENT_VALIDATION_FAILED',independent['issues'])
            for row in locked:
                if row not in rows:return failure('VALIDATION_FAILED','COMMITMENT_CHANGED',row['call_id'])
            payload={'status':'BERTH_SUBSYSTEM_FEASIBLE','solver_status':solver.StatusName(result),'schedule':rows,
            'model_version':MODEL_VERSION,'policy_id':policy.get('policy_id',policy['schema_version']),'event_definition_issues':definition_issues,
            'input_fingerprint':digest(canonical(snapshot).encode()),
            'candidate_domain_fingerprint':digest(canonical({'as_of_at':as_of_at,'candidate_etbs':candidate_etbs,'commitments':locked,**({'pair_candidate_etbs':pair_candidate_etbs} if pair_candidate_etbs is not None else {}),**({'selected_call_ids':sorted(selected_call_ids)} if selected_call_ids is not None else {}),**({'allowed_berths_by_call':allowed_berths_by_call} if allowed_berths_by_call is not None else {}),**({'forbidden_schedules':list(forbidden_schedules)} if forbidden_schedules else {}),**({'completed_rows':list(completed_rows)} if completed_rows else {}),**({'phase_release_policy':phase_release_policy} if phase_release_policy!='ALL_OPTIONS_V1' else {}),**({'search_heuristic':search_heuristic} if search_heuristic!='NONE' else {})}).encode()),'policy_fingerprint':digest(canonical(policy).encode()),
            'boundaries':{'as_of_at':begin.isoformat(),'optimization_end_at':end.isoformat(),'freeze_end_at':(begin+timedelta(hours=24)).isoformat()},
            'completed_call_ids':sorted(completed),
            'outside_horizon_call_ids':outside,
            'future_fix_call_ids':[cid for cid in outside if calls[cid]['fixed_etb_is_hard']],
            'pending_validation':['TANK_INVENTORY_AND_TRANSFER_RATES','CONTRACT_SETTLEMENT','EXTERNAL_WEATHER_BEYOND_SOURCE_RESTRICTIONS'] + (['FUTURE_FIX_ASSIGNMENTS_BEYOND_DETAILED_HORIZON'] if any(calls[cid]['fixed_etb_is_hard'] for cid in outside) else []),
            'option_count':len(options),'pair_conflict_count':conflicts,'pair_checks':pair_checks,'conflict_filter':conflict_filter,'phase_release_policy':phase_release_policy,'search_heuristic':search_heuristic,'solver_objective_scope':('HEURISTIC_SEARCH_ORDER_NOT_DEMURRAGE' if search_heuristic!='NONE' else 'FEASIBILITY_ONLY'),
            'independent_validation':independent,'seconds':time.perf_counter()-started,
            'scope':('Feasibility over supplied ETBs and source vessel/berth-rate durations; not integrated tank feasibility or demurrage optimization' if policy.get('cargo_duration_policy')=='VESSEL_BERTH_RATE_V1' else 'Feasibility over supplied ETBs and berth-rate lower-bound cargo durations; not integrated tank feasibility or demurrage optimization'),
            'operational_approval':False}
            if schema=='DOCKTIMIZE_NORMALIZED_INPUT_V5':payload['cp_sat_seconds']=solver.WallTime()

            if candidate_visitor is None:return payload
            emitted+=1
            if candidate_visitor(payload) is False:return {'status':'ENUMERATION_STOPPED','emitted':emitted,'operational_approval':False}
            if not selected:return {'status':'ENUMERATION_EXHAUSTED','emitted':emitted,'operational_approval':False}
            m.Add(sum(x[i] for i in selected)<=len(selected)-1)
    except TimeoutError as exc:return failure('TIME_LIMIT_NO_INCUMBENT','TIME_BUDGET_EXHAUSTED',exc,scope='No feasibility claim')
    except ImportError as exc:return failure('SOLVER_UNAVAILABLE','SOLVER_UNAVAILABLE',exc)
    except (ValueError,KeyError,TypeError,OverflowError) as exc:return failure('INVALID_INPUT','MISSING_OR_INVALID_PHYSICAL_POLICY',exc)
