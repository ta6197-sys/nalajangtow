"""V5 finite-domain physical scheduling followed by exact inventory validation.

The workbook and snapshot are verified before their physical tables are used.
Only explicitly selected calls are optimized; source calls are never deleted.
"""
from copy import deepcopy
from datetime import timedelta
import io
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


def schedule_v5(snapshot, *, data_dir, call_ids, as_of_at, candidate_etbs, policy,
                time_limit_seconds=30, projection_end_at=None, initial_inventory=None,
                carried_transfers=None, tank_commitments=None, pilotage_context=None):
    """Validate and schedule a finite V5 subset; no schedule-wide feasibility claim."""
    started=time.perf_counter();domain={'as_of_at':as_of_at,'call_ids':sorted(call_ids),'candidate_etbs':candidate_etbs}
    result={'scenario_id':'V5_3TANK','model_version':VERSION,'source_fingerprint':digest(canonical(snapshot).encode()),
            'policy_fingerprint':digest(canonical(policy).encode()),'candidate_domain_fingerprint':digest(canonical(domain).encode()),
            'solver_status':'NOT_RUN','physical_validation':'NOT_EVALUATED','inventory_validation':'NOT_EVALUATED',
            'combined_validation':'NOT_VALIDATED','scheduled_calls':[],'unscheduled_calls':[],
            'selected_berths':{},'selected_etbs':{},'inventory_result':None,'issues':[],
            'operational_approval':False,'scope':'Selected calls and supplied finite ETB candidates only; full scheduling and settlement not evaluated'}
    def finish(status,code=None,reason=None,**details):
        result['status']=status
        if code:result['issues'].append({'code':code,'reason':str(reason),**details})
        result['runtime_seconds']=time.perf_counter()-started
        return result
    try:
        if snapshot.get('scenario_id')!='V5_3TANK' or snapshot.get('schema_version')!='DOCKTIMIZE_NORMALIZED_INPUT_V5':return finish('INVALID_INPUT','INVALID_V5_INPUT','Expected normalized V5 input')
        work=_verified_snapshot(snapshot,data_dir)
        calls={c['call_id']:c for c in work['calls']}
        if not call_ids or len(set(call_ids))!=len(call_ids) or not set(call_ids)<=set(calls):return finish('INVALID_INPUT','INVALID_V5_INPUT','Invalid selected call IDs')
        result['unscheduled_calls']=sorted(calls)
        eligible={cid:eligible_berth_ids(calls[cid],work['berths'],work['tanks']) for cid in call_ids}
        if not isinstance(policy,dict) or policy.get('cargo_duration_policy')!='VESSEL_BERTH_RATE_V1':return finish('UNSUPPORTED_OPERATIONAL_POLICY','UNSUPPORTED_OPERATIONAL_POLICY','Explicit VESSEL_BERTH_RATE_V1 and complete operating policy required')
        policy,pilot_issue=prepare_pilotage_policy(work,call_ids,eligible,policy,pilotage_context,as_of_at,candidate_etbs)
        if pilot_issue:
            result['issues'].append(pilot_issue);return finish('UNSUPPORTED_OPERATIONAL_POLICY')
        result['policy_fingerprint']=digest(canonical(policy).encode())
        try:validate_policy(policy,[b['berth_id'] for b in work['berths'] if b.get('active')=='Y'])
        except (ValueError,KeyError,TypeError) as exc:return finish('UNSUPPORTED_OPERATIONAL_POLICY','UNSUPPORTED_OPERATIONAL_POLICY',exc)
        used={b for ids in eligible.values() for b in ids}
        groups=work['workbook_tables']['groups']['rows'];retained=[]
        for group in groups:
            members=set(tokens(group.get('berth_ids')))
            if group.get('rule_type')=='PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP':
                if members & used:return finish('UNSUPPORTED_OPERATIONAL_POLICY','UNSUPPORTED_OPERATIONAL_POLICY','Source 4-1/4-2 event and GRT exception definitions remain unresolved',berth_ids=sorted(members & used))
                continue
            retained.append(group)
        work['workbook_tables']['groups']['rows']=retained
        physical=solve_berth_schedule(work,as_of_at=as_of_at,candidate_etbs=candidate_etbs,policy=policy,time_limit_seconds=time_limit_seconds,selected_call_ids=call_ids,allowed_berths_by_call=eligible,_v5_context=_V5_VALIDATED_CONTEXT)
        result['solver_status']=physical.get('solver_status',physical['status']);result['candidate_domain_fingerprint']=physical.get('candidate_domain_fingerprint',result['candidate_domain_fingerprint']);result['physical_solver_result']=physical
        if physical['status']!='BERTH_SUBSYSTEM_FEASIBLE':
            result['issues']=physical.get('issues',[])
            if any(i.get('code')=='NO_FEASIBLE_GENERATED_OPTION' for i in result['issues']):return finish('NO_FEASIBLE_GENERATED_OPTION')
            if physical['status']=='INFEASIBLE' and all(calls[cid].get('fixed_etb_is_hard') is True for cid in call_ids):
                return finish('INFEASIBLE','FIX_PRECHECK_FAILED','Fixed calls conflict within the supplied candidate domain',call_ids=sorted(call_ids))
            return finish(physical['status'])
        rows=physical['schedule'];independent=validate_result(work,rows,policy);result['physical_validation']=independent
        if independent.get('status')!='VALID':
            result['issues']=independent.get('issues',[]);return finish('PHYSICAL_VALIDATION_FAILED')
        for row in rows:
            if row['berth_id'] not in eligible[row['call_id']]:return finish('PHYSICAL_VALIDATION_FAILED','MISSING_TANK_CONNECTION','Selected berth is not statically eligible',call_id=row['call_id'],berth_id=row['berth_id'])
        result['scheduled_calls']=[r['call_id'] for r in rows];result['unscheduled_calls']=sorted(set(calls)-set(result['scheduled_calls']))
        result['selected_berths']={r['call_id']:r['berth_id'] for r in rows};result['selected_etbs']={r['call_id']:r['etb_at'] for r in rows}
        if set(result['scheduled_calls'])!=set(call_ids):return finish('INTEGRATION_VALIDATION_FAILED','NO_FEASIBLE_GENERATED_OPTION','Not every selected call was scheduled within the candidate horizon')
        end=timestamp(projection_end_at) if projection_end_at else max([timestamp(as_of_at)+timedelta(hours=72)]+[timestamp(r['work_end_at']) for r in rows])
        if end<max(timestamp(r['work_end_at']) for r in rows):return finish('INTEGRATION_VALIDATION_FAILED','INVALID_FLOW_COVERAGE','Projection must cover all vessel transfers')
        inv=validate_v5_candidate(work,rows,as_of_at=as_of_at,projection_end_at=end.isoformat(),initial_inventory=initial_inventory,carried_transfers=carried_transfers,tank_commitments=tank_commitments,same_tank_policy='ALLOW')
        result['inventory_result']=inv;result['inventory_validation']=inv['status']
        if inv['status']!='INVENTORY_VALID':result['issues']=inv.get('issues',[]);return finish('INVENTORY_VALIDATION_FAILED')
        assignments={x['call_id']:x for x in inv['tank_result']['assignments']}
        definitions={x['call_id']:x for x in inv['tank_result']['transfer_definitions']}
        if set(assignments)!=set(result['scheduled_calls']) or set(definitions)!=set(result['scheduled_calls']):return finish('INTEGRATION_VALIDATION_FAILED','INTEGRATION_VALIDATION_FAILED','Physical and inventory call sets differ')
        for row in rows:
            x=definitions[row['call_id']]
            if (x['berth_id']!=row['berth_id'] or timestamp(x['etb_at'])!=timestamp(row['etb_at']) or timestamp(x['start_at'])!=timestamp(row['work_intervals'][0][0]) or timestamp(x['end_at'])!=timestamp(row['work_intervals'][-1][1])):
                return finish('INTEGRATION_VALIDATION_FAILED','INTEGRATION_VALIDATION_FAILED','Physical and inventory transfer definitions differ',call_id=row['call_id'])
        result['combined_validation']='PHYSICAL_AND_INVENTORY_VALID';return finish('PHYSICAL_AND_INVENTORY_VALID')
    except (OSError,ValueError,TypeError,KeyError,ImportError) as exc:return finish('INVALID_INPUT','INVALID_V5_INPUT',exc)
