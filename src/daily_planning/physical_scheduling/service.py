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


def solve_berth_schedule(snapshot,*,as_of_at,candidate_etbs,policy,commitments=(),time_limit_seconds=30):
    started=time.perf_counter()
    def failure(status,code,reason,**details):
        return {'status':status,'issues':[dict(code=code,reason=str(reason),**details)],
                'model_version':MODEL_VERSION,'operational_approval':False}
    try:
        begin=timestamp(as_of_at);end=begin+timedelta(hours=72)
        if isinstance(time_limit_seconds,bool) or not math.isfinite(time_limit_seconds) or time_limit_seconds<=0:raise ValueError('Invalid solver budget')
        if snapshot.get('schema_version')!='DOCKTIMIZE_NORMALIZED_INPUT_V3':raise ValueError('Normalized V3 snapshot required')
        if 'source_input' in snapshot:
            from ..integrated_input.validator import validate
            preflight=validate(snapshot)
            if not preflight['data_preflight_pass']:return failure('INVALID_INPUT','V3_DATA_PREFLIGHT_FAILED',preflight['issues'])
        calls={c['call_id']:c for c in snapshot['calls']};berths={b['berth_id']:b for b in snapshot['berths']}
        if len(calls)!=len(snapshot['calls']) or len(berths)!=len(snapshot['berths']):raise ValueError('Duplicate source identity')
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
        check=validate_result(snapshot,locked,policy)
        if check['status']!='VALID':return failure('INVALID_INPUT','COMMITMENT_VALIDATION_FAILED',check['issues'])
        outside=[];options=[];groups=[];rejected=[]
        for cid,call in sorted(calls.items()):
            if cid in locked_ids:continue
            eligible_time=timestamp(call['fix_etb_at']) if call['fixed_etb_is_hard'] else timestamp(call['eta_at'])
            if eligible_time>=end:outside.append(cid);continue
            if call['fixed_etb_is_hard'] and eligible_time<begin:return failure('INFEASIBLE','FIX_PRECHECK_FAILED','Past FIX must have an explicit carried commitment',call_id=cid)
            times=[call['fix_etb_at']] if call['fixed_etb_is_hard'] else candidate_etbs.get(cid,[])
            times=sorted({timestamp(value) for value in times})
            selected=[]
            for berth in active:
                if compatibility(call,berth):continue
                for at in times:
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
        dock=set(policy.get('dock_4142_berths',[]))
        for base in base_options:
            release_times=sorted({row['switching_end_at'] for row in base_options+locked
                                  if row['call_id']!=base['call_id'] and (row['berth_id']==base['berth_id'] or {row['berth_id'],base['berth_id']}<=dock)},key=timestamp)
            for release in release_times:
                if timestamp(release)<=timestamp(base['etb_at']):continue
                try:
                    row=replay(calls[base['call_id']],berths[base['berth_id']],base['etb_at'],policy,
                               restrictions=snapshot['workbook_tables']['restrictions']['rows'],hose_connection_start_at=release)
                    if validate_result(snapshot,[row],policy)['status']=='VALID':options.append(row)
                except ValueError:continue
        unique={canonical(row):row for row in options}
        options=[row for row in unique.values() if not any(pair_conflicts(row,r,snapshot,policy) for r in locked)]
        groups=[]
        for cid,call in sorted(calls.items()):
            if cid in locked_ids or cid in outside:continue
            indices=[i for i,row in enumerate(options) if row['call_id']==cid]
            if not indices:return failure('INFEASIBLE','FIX_PRECHECK_FAILED' if call['fixed_etb_is_hard'] else 'NO_FEASIBLE_GENERATED_OPTION','All generated options conflict with protected work',call_id=cid)
            groups.append(indices)
        from ortools.sat.python import cp_model
        m=cp_model.CpModel();x=[m.NewBoolVar('choice_'+str(i)) for i in range(len(options))]
        for indices in groups:m.AddExactlyOne(x[i] for i in indices)
        conflicts=0
        for i,a in enumerate(options):
            for j in range(i+1,len(options)):
                b=options[j]
                if a['call_id']!=b['call_id'] and pair_conflicts(a,b,snapshot,policy):m.AddBoolOr([x[i].Not(),x[j].Not()]);conflicts+=1
        tug_intervals=[];demands=[]
        def offset(value):return int((timestamp(value)-begin).total_seconds())
        for i,row in enumerate(options+locked):
            for phase,(a,b) in enumerate([(row['berthing_start_at'],row['etb_at']),(row['unberthing_start_at'],row['etu_at'])]):
                a,b=offset(a),offset(b)
                demand=row['tug_units']['berthing' if phase==0 else 'unberthing']
                if b<=a or not demand:continue
                interval=m.NewOptionalIntervalVar(a,b-a,b,x[i],f'tug_{i}_{phase}') if i<len(options) else m.NewIntervalVar(a,b-a,b,f'locked_tug_{i}_{phase}')
                tug_intervals.append(interval);demands.append(demand)
        if tug_intervals:m.AddCumulative(tug_intervals,demands,policy['tug_capacity'])
        m.Minimize(0)
        solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=float(time_limit_seconds);solver.parameters.num_search_workers=1;solver.parameters.random_seed=42
        result=solver.Solve(m)
        if result not in (cp_model.FEASIBLE,cp_model.OPTIMAL):return failure('INFEASIBLE' if result==cp_model.INFEASIBLE else 'TIME_LIMIT_NO_INCUMBENT','NO_VALIDATED_CANDIDATE',solver.StatusName(result),scope='Generated options only')
        rows=locked+[row for i,row in enumerate(options) if solver.Value(x[i])]
        rows.sort(key=lambda row:(timestamp(row['etb_at']),row['call_id']))
        independent=validate_result(snapshot,rows,policy)
        if independent['status']!='VALID':return failure('VALIDATION_FAILED','INDEPENDENT_VALIDATION_FAILED',independent['issues'])
        for row in locked:
            if row not in rows:return failure('VALIDATION_FAILED','COMMITMENT_CHANGED',row['call_id'])
        return {'status':'BERTH_SUBSYSTEM_FEASIBLE','solver_status':solver.StatusName(result),'schedule':rows,
            'model_version':MODEL_VERSION,'policy_id':policy.get('policy_id',policy['schema_version']),'event_definition_issues':definition_issues,
            'input_fingerprint':digest(canonical(snapshot).encode()),
            'candidate_domain_fingerprint':digest(canonical({'as_of_at':as_of_at,'candidate_etbs':candidate_etbs,'commitments':locked}).encode()),'policy_fingerprint':digest(canonical(policy).encode()),
            'boundaries':{'as_of_at':begin.isoformat(),'optimization_end_at':end.isoformat(),'freeze_end_at':(begin+timedelta(hours=24)).isoformat()},
            'outside_horizon_call_ids':outside,
            'future_fix_call_ids':[cid for cid in outside if calls[cid]['fixed_etb_is_hard']],
            'pending_validation':['TANK_INVENTORY_AND_TRANSFER_RATES','CONTRACT_SETTLEMENT','EXTERNAL_WEATHER_BEYOND_SOURCE_RESTRICTIONS'] + (['FUTURE_FIX_ASSIGNMENTS_BEYOND_DETAILED_HORIZON'] if any(calls[cid]['fixed_etb_is_hard'] for cid in outside) else []),
            'option_count':len(options),'pair_conflict_count':conflicts,
            'independent_validation':independent,'seconds':time.perf_counter()-started,
            'scope':'Feasibility over supplied ETBs and berth-rate lower-bound cargo durations; not integrated tank feasibility or demurrage optimization',
            'operational_approval':False}
    except ImportError as exc:return failure('SOLVER_UNAVAILABLE','SOLVER_UNAVAILABLE',exc)
    except (ValueError,KeyError,TypeError,OverflowError) as exc:return failure('INVALID_INPUT','MISSING_OR_INVALID_PHYSICAL_POLICY',exc)
