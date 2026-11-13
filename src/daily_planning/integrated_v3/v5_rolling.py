"""Resumable synthetic V5 rolling projection with persistent dynamic locks."""
from copy import deepcopy
from datetime import timedelta
from fractions import Fraction
from pathlib import Path
from time import monotonic
from ..integrated_input.provenance import canonical,digest
from ..integrated_input.validator import timestamp,number
from ..integrated_input.v5 import eligible_berth_ids
from ..physical_scheduling.validator import validate_result
from ..tank.v5_inventory import validate_v5_inventory
from . import v5_physical
from .coordinator import validate_v5_candidate
from ..physical_scheduling.v5_user_rules import VERSION as USER_V5_VERSION
from .v5_cost_search import optimize_v5
from .v5_settlement import price_v5_schedule
from .v5_locks import apply_overrides,resolve_locks
from .v5_inventory_risk import RISK_VERSION, classify_inventory_points
from .v5_deferral import DEFERRAL_VERSION, choose_admission, advance_history, verify_history
from .v5_pilotage_gap import PILOTAGE_GAP_VERSION, collect_certificates
from .v5_failure_evidence import build_failure_evidence,write_failure_evidence_new
from .resumable_rolling import _read,_write,_fingerprint

VERSION='V5_DYNAMIC_LOCK_ROLLING_V1'


def _amount_at(transfer,boundary):
    total=Fraction(0)
    for seg in transfer['segments']:
        a=timestamp(seg['start_at']);b=min(timestamp(seg['end_at']),boundary)
        if b>a:total+=number(seg['rate_bbl_per_hour'])*Fraction(int((b-a).total_seconds()),3600)
    return total


def _transition(snapshot, *, at, prior, rows, tank, pricing,settlement_snapshot,pricing_policy,clock_policy,settlement_records):
    """Recompute the 24h state and reconcile against the full 72h replay."""
    start=timestamp(at);boundary=start+timedelta(hours=24)
    defs=tank.get('transfer_definitions',[])
    relevant=[d for d in defs if timestamp(d['start_at'])<boundary and timestamp(d['end_at'])>start]
    ids={d['call_id'] for d in relevant}
    carried=[x for x in prior.get('carried_transfers',[]) if x['call_id'] in ids]
    commits=[x for x in prior.get('tank_commitments',[]) if x['call_id'] in ids]
    projected=validate_v5_inventory(snapshot,relevant,start_at=start.isoformat(),end_at=boundary.isoformat(),initial_inventory=prior.get('inventory'),carried_transfers=carried,commitments=commits,same_tank_policy='ALLOW')
    if projected['status']!='VALID':raise ValueError('CARRYOVER_REPLAY_FAILED: '+str(projected['issues']))
    endstate=projected['end_state']
    if tank.get('carried_inventory_bbl')!=endstate['inventory_bbl']:raise ValueError('CARRYOVER_INVENTORY_MISMATCH')
    def ordered(xs):return sorted(xs,key=lambda x:x['call_id'])
    if ordered(tank.get('carried_transfers',[]))!=ordered(endstate['carried_transfers']):raise ValueError('CARRYOVER_TRANSFER_MISMATCH')
    completed={r['call_id']:deepcopy(r) for r in prior.get('completed_rows',[])}
    for row in rows:
        if (timestamp(row['etu_at'])<=boundary if row.get('phase_policy_version')==USER_V5_VERSION else max(timestamp(row['physical_clearance_at']),timestamp(row['switching_end_at']))<=boundary):completed[row['call_id']]=deepcopy(row)
    pending_rows=[deepcopy(r) for r in rows if r['call_id'] not in completed]
    qty=deepcopy(prior.get('cargo_progress',{}))
    for d in defs:
        cid=d['call_id'];all_qty=number(next(c['cargo_volume_bbl'] for c in snapshot['calls'] if c['call_id']==cid));done=_amount_at(d,boundary)
        if done>all_qty:raise ValueError('CARGO_CONSERVATION_FAILED: '+cid)
        qty[cid]={'completed_bbl':str(done),'remaining_bbl':str(all_qty-done)}
    # A completed transfer is not re-integrated in later windows.
    for cid,row in completed.items():
        if cid not in qty:
            volume=number(next(c['cargo_volume_bbl'] for c in snapshot['calls'] if c['call_id']==cid));qty[cid]={'completed_bbl':str(volume),'remaining_bbl':'0'}
    accounting=deepcopy(prior.get('contract_accounting_state',{}))
    for cid in completed:
        if cid in accounting:continue
        item=pricing.get(cid)
        if not item or item.get('status')!='SETTLED':raise ValueError('COMPLETED_SETTLEMENT_UNRESOLVED: '+cid)
        accounting[cid]={'demurrage_cents':item['demurrage_cents'],'settlement':deepcopy(item)}
    in_progress={}
    for row in pending_rows:
        cid=row['call_id']
        if timestamp(row['berthing_start_at'])>=boundary:continue
        if timestamp(row['work_start_at'])>=boundary:
            in_progress[cid]={'status':'AWAITING_WORK_COMMENCEMENT','through_at':boundary.isoformat(),'accrued_demurrage_cents':None}
            continue
        if timestamp(row['work_end_at'])<=boundary:
            item=pricing.get(cid)
            if not item or item.get('status')!='SETTLED':raise ValueError('ONGOING_SETTLEMENT_UNRESOLVED: '+cid)
            in_progress[cid]={'status':'WORK_COMPLETED_BERTH_OCCUPIED','through_at':boundary.isoformat(),'accrued_demurrage_cents':item['demurrage_cents'],'settlement':deepcopy(item)}
            continue
        partial=deepcopy(row);partial['work_end_at']=boundary.isoformat();partial['work_intervals']=[[a,min(timestamp(b),boundary).isoformat()] for a,b in row['work_intervals'] if timestamp(a)<boundary]
        partial_records=[]
        for item in settlement_records:
            if item.get('call_id')==cid and item.get('work_start_at')==row['work_start_at'] and item.get('finish_at')==row['work_end_at']:
                item=deepcopy(item);item['finish_at']=boundary.isoformat();item['event_intervals']=[[a,min(timestamp(b),boundary).isoformat(),code] for a,b,code in item['event_intervals'] if timestamp(a)<boundary];partial_records.append(item)
        priced=price_v5_schedule(settlement_snapshot,[partial],as_of_at=at,settlement_records=partial_records,pricing_policy=pricing_policy,clock_policy=clock_policy)
        if priced['status']!='SETTLED':raise ValueError('ONGOING_SETTLEMENT_UNRESOLVED: '+str(priced['issues']))
        item=priced['vessel_demurrage_breakdown'][cid];in_progress[cid]={'status':'IN_PROGRESS','through_at':boundary.isoformat(),'accrued_demurrage_cents':item['demurrage_cents'],'settlement':item}
    kept={r['call_id'] for r in pending_rows}
    locked_defs=[deepcopy(d) for d in defs if d['call_id'] in kept and timestamp(d['end_at'])>boundary]
    return {'at':boundary.isoformat(),'inventory':endstate['inventory_bbl'],'carried_transfers':endstate['carried_transfers'],
            'tank_commitments':locked_defs,'completed_rows':list(completed.values()),'previous_schedule':pending_rows,
            'cargo_progress':qty,'contract_accounting_state':accounting,'in_progress_contract_accounting':in_progress,'lock_state':deepcopy(prior.get('lock_state',{})),
            'state_origin':'SIMULATED_PROJECTION_NOT_OBSERVATION','operational_approval':False,
            **({'deferral_history':deepcopy(prior['deferral_history'])} if 'deferral_history' in prior else {})}


def _empty_tank(snapshot,at,prior):
    boundary=timestamp(at)+timedelta(hours=24)
    r=validate_v5_inventory(snapshot,[],start_at=at,end_at=boundary.isoformat(),initial_inventory=prior.get('inventory'),carried_transfers=prior.get('carried_transfers'),commitments=prior.get('tank_commitments'),same_tank_policy='ALLOW')
    if r['status']!='VALID':raise ValueError('EMPTY_WINDOW_INVENTORY_INVALID: '+str(r['issues']))
    # The 24-hour boundary is the end of this independent exact replay.
    r['carried_inventory_bbl']=r['end_state']['inventory_bbl'];r['carried_transfers']=r['end_state']['carried_transfers']
    return r


def run_v5_rolling(snapshot, *, data_dir, policy, pilotage_context, as_of_at, output_dir,
                   call_ids=None, windows=3, time_limit_seconds=30, max_cost_candidates=5,
                   pricing_policy='EXPLICIT_EVENTS_V1',clock_policy='BLOCK_UNVERIFIED_CLOCK',
                   settlement_records=(),approved_commitments=(),override_events=None,manual_overrides=None,
                   resume=False,max_new_windows=None,rolling_optimizer_mode='LEGACY_ENUMERATION',
                   max_improvement_rounds=2,max_candidates_per_pair=12,max_total_candidates=2000,tie_break_mode='NONE',inventory_policy_version=None,
                   inventory_risk_policy_version=None,deferral_policy_version=None,max_deferral_probes=8,
                   horizon_pilotage_policy_version=None):
    """Advance only verified 24h transitions; all produced state is synthetic."""
    started=monotonic();start=timestamp(as_of_at);folder=Path(output_dir)
    source_root=Path(data_dir).resolve();output_root=folder.resolve()
    if output_root==source_root or source_root in output_root.parents:raise ValueError('Checkpoint directory must not be inside the source data directory')
    if type(windows) is not int or windows<1:raise ValueError('Positive window count required')
    if max_new_windows is not None and (type(max_new_windows) is not int or max_new_windows<1):raise ValueError('Invalid max_new_windows')
    if rolling_optimizer_mode not in ('LEGACY_ENUMERATION','INTEGRATED_COST_AWARE_V1'):raise ValueError('Invalid rolling optimizer mode')
    if tie_break_mode not in ('NONE','CANONICAL_ETB_BERTH_V1'):raise ValueError('Invalid stable tie-break mode')
    if tie_break_mode!='NONE' and rolling_optimizer_mode!='INTEGRATED_COST_AWARE_V1':raise ValueError('Tie-break requires integrated V5 mode')
    if rolling_optimizer_mode=='INTEGRATED_COST_AWARE_V1' and (type(max_improvement_rounds) is not int or not 1<=max_improvement_rounds<=2 or type(max_candidates_per_pair) is not int or not 1<=max_candidates_per_pair<=24 or type(max_total_candidates) is not int or max_total_candidates<1):raise ValueError('Invalid integrated candidate limits')
    calls={c['call_id']:c for c in snapshot['calls']};scope=list(call_ids) if call_ids is not None else sorted(calls)
    if not scope or len(scope)!=len(set(scope)) or not set(scope)<=set(calls):raise ValueError('Invalid call scope')
    if not {r['call_id'] for r in approved_commitments}<=set(scope):raise ValueError('Approved commitment outside call scope')
    from .v5_inventory_policy import policy_view
    if inventory_policy_version is not None and rolling_optimizer_mode!='INTEGRATED_COST_AWARE_V1':raise ValueError('Inventory policy requires integrated rolling mode')
    if inventory_risk_policy_version is not None and (inventory_risk_policy_version!=RISK_VERSION or inventory_policy_version is None):
        raise ValueError('Risk classification requires opt-in physical bounds')
    if deferral_policy_version is not None and (deferral_policy_version!=DEFERRAL_VERSION or rolling_optimizer_mode!='INTEGRATED_COST_AWARE_V1' or snapshot.get('scenario_id')!='V5_BALANCED_154'):
        raise ValueError('Safe admission requires integrated BALANCED')
    if type(max_deferral_probes) is not int or not 1<=max_deferral_probes<=20:
        raise ValueError('Invalid deferral attempt count')
    if horizon_pilotage_policy_version is not None and (horizon_pilotage_policy_version!=PILOTAGE_GAP_VERSION or deferral_policy_version!=DEFERRAL_VERSION):
        raise ValueError('Horizon pilotage deferral requires explicitly opted-in Stage 2B safe deferral')
    work=policy_view(v5_physical._verified_snapshot(snapshot,data_dir),inventory_policy_version)
    inventory_snapshot=work if inventory_policy_version is not None else snapshot
    daylight_path=Path(pilotage_context['daylight_path']);daylight_sha=digest(daylight_path.read_bytes())
    config={'version':VERSION,'source_fingerprint':_fingerprint(snapshot),'policy_fingerprint':_fingerprint({'policy':policy,'pilotage_context':pilotage_context,'daylight_sha256':daylight_sha,'pricing_policy':pricing_policy,'clock_policy':clock_policy,'settlement_records':settlement_records}),'start_at':start.isoformat(),'scope':sorted(scope),'windows':windows,'time_limit_seconds':time_limit_seconds,'max_cost_candidates':max_cost_candidates,'pricing_policy':pricing_policy,'clock_policy':clock_policy,'approved_commitments':list(approved_commitments),'override_events':override_events or {},'planning_hours':72,'step_hours':24,'dynamic_lock_hours':5}
    if rolling_optimizer_mode=='INTEGRATED_COST_AWARE_V1':config.update(rolling_optimizer_mode=rolling_optimizer_mode,objective_mode='MIN_TOTAL_DEMURRAGE',inventory_constraint_mode='EXACT_EVENT_BALANCE_V1',candidate_generation_mode='AUTO_ADAPTIVE_CANDIDATES',candidate_improvement_mode='COST_AWARE_V1',max_improvement_rounds=max_improvement_rounds,max_candidates_per_pair=max_candidates_per_pair,max_total_candidates=max_total_candidates)
    if tie_break_mode!='NONE':config['tie_break_mode']=tie_break_mode
    if inventory_policy_version is not None:config['inventory_policy_version']=inventory_policy_version
    if inventory_risk_policy_version is not None:config['inventory_risk_policy_version']=inventory_risk_policy_version
    if deferral_policy_version is not None:
        config.update(deferral_policy_version=deferral_policy_version,max_deferral_probes=max_deferral_probes)
    if horizon_pilotage_policy_version is not None:config['horizon_pilotage_policy_version']=horizon_pilotage_policy_version
    manifest={'run_fingerprint':_fingerprint(config),'config':config,'operational_approval':False};manifest_path=folder/'manifest.json'
    if snapshot.get('scenario_id')=='V5_BALANCED_154':manifest['scenario_id']='V5_BALANCED_154'
    if resume:
        if not manifest_path.is_file():raise ValueError('Resume source, policy or configuration fingerprint mismatch')
        saved_manifest=_read(manifest_path)
        # Early BALANCED checkpoints predate the explicit manifest label; their
        # source fingerprint still binds the complete normalized scenario.
        legacy_balanced=(snapshot.get('scenario_id')=='V5_BALANCED_154' and 'scenario_id' not in saved_manifest and
                         saved_manifest=={k:v for k,v in manifest.items() if k!='scenario_id'})
        if saved_manifest!=manifest and not legacy_balanced:raise ValueError('Resume source, policy or configuration fingerprint mismatch')
    else:
        if folder.exists() and any(folder.iterdir()):raise FileExistsError('Use a new output directory or resume')
        _write(manifest_path,manifest)
    state={'at':start.isoformat(),'inventory':None,'carried_transfers':[],'tank_commitments':[],'completed_rows':[],'previous_schedule':[],'cargo_progress':{},'contract_accounting_state':{},'in_progress_contract_accounting':{},'lock_state':{},'lock_decisions':{},'state_origin':'SIMULATED_PROJECTION_NOT_OBSERVATION','operational_approval':False}
    if deferral_policy_version is not None:state['deferral_history']={}
    results=[];events=override_events or {}
    def risk_from(tank):
        return classify_inventory_points(work['tanks'],tank['inventories'],version=inventory_risk_policy_version)
    def assert_risk(saved,tank):
        if inventory_risk_policy_version is not None and saved.get('summary',{}).get('inventory_risk_v1')!=risk_from(tank):
            raise ValueError('Risk checkpoint/replay mismatch')
    def finalize_locks(nextstate):
        done={r['call_id'] for r in nextstate['completed_rows']}
        after=resolve_locks([calls[cid] for cid in scope if cid not in done],as_of_at=nextstate['at'],previous_schedule=nextstate['previous_schedule'],lock_state=nextstate['lock_state'],approved_commitments=[r for r in approved_commitments if r['call_id'] not in done],allow_unscheduled_auto=deferral_policy_version is not None)
        if after['issues']:raise ValueError('DYNAMIC_LOCK_PENDING: '+str(after['issues']))
        ids={r['call_id'] for r in after['physical_commitments']}
        if not {r['call_id'] for r in nextstate['carried_transfers']}<=ids:raise ValueError('CARRIED_TRANSFER_NOT_PROTECTED')
        nextstate['tank_commitments']=[d for d in nextstate['tank_commitments'] if d['call_id'] in ids]
        nextstate['lock_state']=after['lock_state']
        nextstate['lock_decisions']=after['decisions']
        return nextstate,after
    def checked_transition(saved,previous):
        at=saved['as_of_at'];rows=saved['selected_schedule'];data=saved['candidate'];boundary=(timestamp(at)+timedelta(hours=24)).isoformat()
        if rows:
            selected=[r['call_id'] for r in rows];eligible={cid:eligible_berth_ids(calls[cid],work['berths'],work['tanks']) for cid in selected}
            history_start=min((r['pob_at'] for r in saved['protected_commitments_in']),key=timestamp,default=None)
            applied,issue=v5_physical.prepare_pilotage_policy(work,selected,eligible,policy,pilotage_context,at,{},history_start)
            if issue:raise ValueError('POLICY_REPLAY_FAILED: '+str(issue))
            physical=validate_result(work,rows,applied)
            if physical['status']!='VALID':raise ValueError('PHYSICAL_REPLAY_FAILED: '+str(physical['issues']))
            prior_physical=saved['protected_commitments_in']
            projection={'projection_end_at':data['tank_result']['projection_end_at']} if rolling_optimizer_mode=='INTEGRATED_COST_AWARE_V1' else {}
            replayed=validate_v5_candidate(work,rows,as_of_at=at,initial_inventory=previous.get('inventory'),carried_transfers=previous.get('carried_transfers'),tank_commitments=previous.get('tank_commitments'),same_tank_policy='ALLOW',physical_commitments=prior_physical,**projection)
            if replayed['status']!='INVENTORY_VALID':raise ValueError('INVENTORY_REPLAY_FAILED: '+str(replayed['issues']))
            tank=replayed['tank_result'];original=data['tank_result'] if rolling_optimizer_mode=='INTEGRATED_COST_AWARE_V1' else data['schedule_result']['final_inventory_result']['tank_result']
            for key in ('inventories','inventory_extrema','external_net_flow_totals_bbl','transfer_definitions','carried_inventory_bbl','carried_transfers'):
                if tank.get(key)!=original.get(key):raise ValueError('INVENTORY_REPLAY_MISMATCH: '+key)
            pricing=price_v5_schedule(work,rows,as_of_at=at,settlement_records=settlement_records,pricing_policy=pricing_policy,clock_policy=clock_policy)
            if pricing['status']!='SETTLED':raise ValueError('SETTLEMENT_REPLAY_UNRESOLVED: '+str(pricing['issues']))
            if pricing['total_demurrage_cents']!=data['total_demurrage_cents'] or pricing['vessel_demurrage_breakdown']!=data['vessel_demurrage_breakdown']:raise ValueError('SETTLEMENT_REPLAY_MISMATCH')
            assert_risk(saved,tank)
            return finalize_locks(_transition(inventory_snapshot,at=at,prior=previous,rows=rows,tank=tank,pricing=pricing['vessel_demurrage_breakdown'],settlement_snapshot=work,pricing_policy=pricing_policy,clock_policy=clock_policy,settlement_records=settlement_records))[0]
        tank=_empty_tank(inventory_snapshot,at,previous)
        assert_risk(saved,tank)
        return finalize_locks(_transition(inventory_snapshot,at=at,prior=previous,rows=[],tank=tank,pricing={},settlement_snapshot=work,pricing_policy=pricing_policy,clock_policy=clock_policy,settlement_records=settlement_records))[0]
    for i in range(1,windows+1):
        path=folder/('window_%03d.json'%i)
        if not path.exists():
            if any((folder/('window_%03d.json'%j)).exists() for j in range(i+1,windows+1)):raise ValueError('Noncontiguous saved checkpoints')
            break
        saved=_read(path);saved_body=dict(saved);saved_fingerprint=saved_body.pop('checkpoint_fingerprint',None)
        if saved_fingerprint!=_fingerprint(saved_body):raise ValueError('Checkpoint fingerprint mismatch')
        expected_at=(start+timedelta(hours=24*(i-1))).isoformat()
        if saved.get('window_id')!=i or saved.get('as_of_at')!=expected_at or saved.get('run_fingerprint')!=manifest['run_fingerprint'] or saved.get('prior_state_fingerprint')!=_fingerprint(state):raise ValueError('Checkpoint continuity mismatch')
        previous=deepcopy(state);changes=dict(events.get(expected_at,{}));changes.update(saved.get('manual_overrides',{}));previous['lock_state']=apply_overrides(previous['lock_state'],changes,scope)
        done={r['call_id'] for r in previous['completed_rows']};locks=resolve_locks([calls[cid] for cid in scope if cid not in done],as_of_at=expected_at,previous_schedule=previous['previous_schedule'],lock_state=previous['lock_state'],approved_commitments=[r for r in approved_commitments if r['call_id'] not in done],allow_unscheduled_auto=deferral_policy_version is not None)
        horizon=timestamp(expected_at)+timedelta(hours=72);expected=[r for r in locks['physical_commitments'] if timestamp(r['berthing_start_at'])<horizon]
        if locks['issues'] or sorted(expected,key=lambda r:r['call_id'])!=sorted(saved['protected_commitments_in'],key=lambda r:r['call_id']) or any(r not in saved['selected_schedule'] for r in expected):raise ValueError('Checkpoint protected commitment mismatch')
        expected_calls={c['call_id'] for c in (calls[cid] for cid in scope if cid not in done) if (timestamp(c['fix_etb_at']) if c['fixed_etb_is_hard'] else timestamp(c['eta_at']))<horizon}|{r['call_id'] for r in expected}
        if deferral_policy_version is None:
            if expected_calls!={r['call_id'] for r in saved['selected_schedule']}:raise ValueError('Checkpoint selected call coverage mismatch')
        else:
            admitted=set(saved.get('admitted_call_ids',[]));deferred=set(saved.get('deferred_call_ids',[]))
            if saved.get('deferral_policy_version')!=deferral_policy_version or set(saved.get('due_call_ids',[]))!=expected_calls:
                raise ValueError('Deferral version or due-call coverage mismatch')
            if admitted!={r['call_id'] for r in saved['selected_schedule']}:raise ValueError('Admitted rows mismatch')
            if horizon_pilotage_policy_version is not None:
                proof=collect_certificates(work,sorted(expected_calls),as_of_at=expected_at,policy=policy,
                    pilotage_context=pilotage_context,protected_ids={r['call_id'] for r in expected},
                    previously_scheduled_ids={r['call_id'] for r in previous['previous_schedule']})
                if (saved.get('horizon_pilotage_policy_version')!=horizon_pilotage_policy_version or
                    saved.get('horizon_pilotage_gap_certificates')!=proof['certificates'] or
                    saved.get('horizon_pilotage_gap_unresolved')!=proof['unresolved'] or
                    not set(proof['certificates'])<=deferred):
                    raise ValueError('Pilotage gap checkpoint proof or deferred partition mismatch')
            updated=verify_history(calls,previous['deferral_history'],saved.get('deferral_history'),
                at=expected_at,due=expected_calls,admitted=admitted,deferred=deferred,
                protected={r['call_id'] for r in expected},
                previous_schedule={r['call_id'] for r in previous['previous_schedule']}) if expected_calls else deepcopy(previous['deferral_history'])
            if saved.get('deferral_history')!=updated:raise ValueError('Deferral history mismatch')
            previous['deferral_history']=updated
        protected_ids={r['call_id'] for r in saved['protected_commitments_in']};previous['tank_commitments']=[d for d in previous['tank_commitments'] if d['call_id'] in protected_ids]
        state=checked_transition(saved,previous)
        if saved.get('next_state_fingerprint')!=_fingerprint(state) or saved.get('next_state')!=state:raise ValueError('Checkpoint carried state mismatch')
        results.append(saved['summary'])
    def report(status,reason=None):
        completed=[r['call_id'] for r in state['completed_rows']];priced=set(state['contract_accounting_state']);progress=state['in_progress_contract_accounting']
        pending={cid for cid in scope if cid not in set(completed)|set(progress) and timestamp(calls[cid]['nor_tender_at'])<=timestamp(state['at'])}
        complete_coverage=results and set(completed)<=priced and not pending and all(v['accrued_demurrage_cents'] is not None for v in progress.values())
        cumulative=(sum(x['demurrage_cents'] for x in state['contract_accounting_state'].values())+sum(x['accrued_demurrage_cents'] for x in progress.values())) if complete_coverage else None
        ongoing={r['call_id'] for r in state['previous_schedule'] if timestamp(r['berthing_start_at'])<=timestamp(state['at'])}
        waiting={cid for cid in scope if cid not in set(completed)|ongoing and timestamp(calls[cid]['eta_at'])<=timestamp(state['at'])}
        future=set(scope)-set(completed)-ongoing-waiting
        return {'status':status,'rolling_run_id':manifest['run_fingerprint'],'window_results':results,'validated_window_count':len(results),'completed_call_ids':sorted(completed),'completed_vessel_ids':sorted(calls[cid]['vessel_id'] for cid in completed),'ongoing_call_ids':sorted(ongoing),'waiting_call_ids':sorted(waiting),'future_call_ids':sorted(future),'protected_call_ids':sorted(cid for cid,d in state['lock_decisions'].items() if d['status']=='LOCKED'),'remaining_call_ids':sorted(set(scope)-set(completed)),
                **({'deferral_policy_version':deferral_policy_version,
                    'deferral_history':deepcopy(state['deferral_history']),
                    'deferred_call_ids':sorted(cid for cid,v in state['deferral_history'].items() if v['status']=='DEFERRED'),
                    'deferred_cost_status':'NOT_PRICED_AS_ZERO'} if deferral_policy_version is not None else {}),
                'lock_state':state['lock_state'],'lock_decisions':state['lock_decisions'],'cargo_progress':state['cargo_progress'],'carried_inventory':state['inventory'],'carried_transfers':state['carried_transfers'],'window_demurrage_results':[r['window_demurrage_cents'] for r in results],'cumulative_demurrage_cents':cumulative,'cumulative_demurrage_scope':'UNIQUE_COMPLETED_PLUS_CURRENT_ACCRUED_WHEN_FULLY_COVERED','unresolved_accounting_call_ids':sorted(pending|{cid for cid,v in progress.items() if v['accrued_demurrage_cents'] is None}),'in_progress_contract_accounting':state['in_progress_contract_accounting'],'carryover_validation':'VALID' if results else 'NOT_EVALUATED','checkpoint_status':'VALIDATED' if results else 'NO_CHECKPOINT','resume_status':'RESUMED' if resume else 'NEW','runtime_seconds':monotonic()-started,'stopping_reason':reason,'state_origin':'SIMULATED_PROJECTION_NOT_OBSERVATION','operational_approval':False}
    fresh=0
    for i in range(len(results)+1,windows+1):
        if max_new_windows is not None and fresh>=max_new_windows:return report('PAUSED','MAX_NEW_WINDOWS')
        at=(start+timedelta(hours=24*(i-1))).isoformat();previous=deepcopy(state);changes=dict(events.get(at,{}));applied_manual=dict(manual_overrides or {}) if fresh==0 else {};changes.update(applied_manual);previous['lock_state']=apply_overrides(previous['lock_state'],changes,scope)
        complete={r['call_id'] for r in previous['completed_rows']};available=[calls[cid] for cid in scope if cid not in complete]
        locks=resolve_locks(available,as_of_at=at,previous_schedule=previous['previous_schedule'],lock_state=previous['lock_state'],approved_commitments=[r for r in approved_commitments if r['call_id'] not in complete],allow_unscheduled_auto=deferral_policy_version is not None)
        if locks['issues']:return report('STOPPED_UNRESOLVED',{'code':'DYNAMIC_LOCK_PENDING','issues':locks['issues']})
        horizon=timestamp(at)+timedelta(hours=72);protected=[r for r in locks['physical_commitments'] if timestamp(r['berthing_start_at'])<horizon]
        protected_ids={r['call_id'] for r in protected};previous['tank_commitments']=[d for d in previous['tank_commitments'] if d['call_id'] in protected_ids]
        selected=sorted({c['call_id'] for c in available if (timestamp(c['fix_etb_at']) if c['fixed_etb_is_hard'] else timestamp(c['eta_at']))<horizon}|{r['call_id'] for r in protected})
        due_call_ids=list(selected);admission=None
        horizon_gap_certificates={};horizon_gap_unresolved={}
        if horizon_pilotage_policy_version is not None and due_call_ids:
            proof=collect_certificates(work,due_call_ids,as_of_at=at,policy=policy,
                pilotage_context=pilotage_context,protected_ids=protected_ids,
                previously_scheduled_ids={r['call_id'] for r in previous['previous_schedule']})
            horizon_gap_certificates=proof['certificates'];horizon_gap_unresolved=proof['unresolved']
            selected=sorted(set(selected)-set(horizon_gap_certificates))
        if selected:
            if rolling_optimizer_mode=='INTEGRATED_COST_AWARE_V1':
                from .v5_rolling_optimizer import optimize_integrated_window
                def attempt_admission(ids,budget):
                    return optimize_integrated_window(snapshot,work,data_dir=data_dir,call_ids=ids,as_of_at=at,policy=policy,pilotage_context=pilotage_context,settlement_records=settlement_records,pricing_policy=pricing_policy,clock_policy=clock_policy,time_limit_seconds=budget,max_improvement_rounds=max_improvement_rounds,max_candidates_per_pair=max_candidates_per_pair,max_total_candidates=max_total_candidates,**({'tie_break_mode':tie_break_mode} if tie_break_mode!='NONE' else {}),**({'inventory_policy_version':inventory_policy_version} if inventory_policy_version is not None else {}),initial_inventory=previous['inventory'],carried_transfers=previous['carried_transfers'],tank_commitments=previous['tank_commitments'],physical_commitments=protected)
                if deferral_policy_version is not None:
                    admission=choose_admission(calls,selected,at=at,protected=protected_ids,
                        previous_schedule={r['call_id'] for r in previous['previous_schedule']},
                        optimize=attempt_admission,total_budget_seconds=time_limit_seconds,
                        max_probes=max_deferral_probes)
                    candidate=admission['candidate']
                    if admission['status']=='VALIDATED':selected=admission['admitted']
                else:candidate=attempt_admission(selected,time_limit_seconds)
                if candidate['status']!='VALIDATED':
                    details={'code':'WINDOW_NOT_VALIDATED','reason':candidate.get('reason'),'issues':candidate.get('issues',[])}
                    if admission is not None:details.update(admission_reason=admission['reason'],admission_attempts=admission['attempts'],protected_or_fix_call_ids=admission.get('protected_or_fix_call_ids',[]))
                    try:
                        failure=build_failure_evidence(window_id=i,as_of_at=at,selected_call_ids=selected,
                            candidate=candidate,source_fingerprint=config['source_fingerprint'],
                            policy_fingerprint=config['policy_fingerprint'],scenario_id=snapshot.get('scenario_id'))
                        write_failure_evidence_new(folder/('window_%03d_failure.json'%i),failure)
                        details['failure_evidence_path']='window_%03d_failure.json'%i
                    except (OSError,ValueError,TypeError,OverflowError) as exc:
                        details['failure_evidence_error']=type(exc).__name__+': '+str(exc)
                    return report('STOPPED_UNRESOLVED',details)
                result=candidate['optimizer_result'];rows=candidate['selected_schedule'];tank=candidate['tank_result'];pricing=candidate['vessel_demurrage_breakdown'];cost=candidate['total_demurrage_cents']
            else:
                result=optimize_v5(snapshot,data_dir=data_dir,call_ids=selected,as_of_at=at,policy=policy,pilotage_context=pilotage_context,settlement_records=settlement_records,pricing_policy=pricing_policy,clock_policy=clock_policy,time_limit_seconds=time_limit_seconds,max_cost_candidates=max_cost_candidates,initial_inventory=previous['inventory'],carried_transfers=previous['carried_transfers'],tank_commitments=previous['tank_commitments'],physical_commitments=protected)
                allowed_stops={'COST_CANDIDATE_LIMIT','FINITE_DOMAIN_EXHAUSTED','PHYSICAL_CANDIDATE_LIMIT'}
                if result.get('optimization_status')!='PRICED_INCUMBENT' or result.get('stopping_reason') not in allowed_stops:return report('STOPPED_UNRESOLVED',{'code':'WINDOW_NOT_VALIDATED','optimization_status':result.get('optimization_status'),'reason':result.get('stopping_reason'),'unresolved_settlement_count':result.get('unresolved_settlement_count')})
                candidate=result['best_candidate'];rows=candidate['schedule'];tank=candidate['schedule_result']['final_inventory_result']['tank_result'];pricing=candidate['vessel_demurrage_breakdown'];cost=candidate['total_demurrage_cents']
        else:
            try:tank=_empty_tank(inventory_snapshot,at,previous)
            except (ValueError,KeyError,TypeError) as exc:return report('STOPPED_UNRESOLVED',{'code':'EMPTY_WINDOW_INVALID','reason':str(exc)})
            candidate=None;rows=[];pricing={};cost=0
        window_deferred=sorted(set(horizon_gap_certificates)|set(admission['deferred'] if admission is not None else []))
        if deferral_policy_version is not None and due_call_ids:
            previous['deferral_history']=advance_history(calls,previous['deferral_history'],
                at=at,due=due_call_ids,admitted=selected,deferred=window_deferred)
        try:nextstate,after=finalize_locks(_transition(inventory_snapshot,at=at,prior=previous,rows=rows,tank=tank,pricing=pricing,settlement_snapshot=work,pricing_policy=pricing_policy,clock_policy=clock_policy,settlement_records=settlement_records))
        except (ValueError,KeyError,TypeError,ZeroDivisionError) as exc:return report('STOPPED_UNRESOLVED',{'code':'CARRYOVER_INVALID','reason':str(exc)})
        summary={'window_id':i,'as_of_at':at,'status':'VALIDATED_SYNTHETIC','window_demurrage_cents':cost,'selected_call_ids':selected,'lock_decisions':locks['decisions'],'next_lock_decisions':after['decisions'],'cost_scope':'WINDOW_OVERLAP_NOT_CUMULATIVE','cost_search_scope':result['optimality_scope'] if selected else 'NO_WORK','cost_candidates_evaluated':(result['solver_attempt_count'] if rolling_optimizer_mode=='INTEGRATED_COST_AWARE_V1' else result['evaluated_schedule_count']) if selected else 0,'optimization_runtime_seconds':(result['runtime_seconds'] if rolling_optimizer_mode=='INTEGRATED_COST_AWARE_V1' else result['search_runtime_seconds']) if selected else 0}
        if selected and rolling_optimizer_mode=='INTEGRATED_COST_AWARE_V1':summary.update(rolling_optimizer_mode=rolling_optimizer_mode,selected_etbs=result['selected_etbs'],initial_candidate_count=result['initial_candidate_count'],final_candidate_count=result['final_candidate_count'],initial_demurrage_cents=result['initial_total_demurrage_cents'],best_demurrage_cents=result['best_total_demurrage_cents'],candidate_expansion_rounds=result['candidate_expansion_rounds'],solver_status=result['solver_status'],final_domain_optimality_proven=result['final_domain_optimality_proven'])
        if deferral_policy_version is not None:
            summary.update(deferral_policy_version=deferral_policy_version,due_call_ids=due_call_ids,
                admitted_call_ids=selected,deferred_call_ids=window_deferred,
                admission_attempts=(admission['attempts'] if admission is not None else []),
                deferral_warnings=[{'call_id':cid,**v} for cid,v in sorted(previous['deferral_history'].items())
                    if cid in due_call_ids and v['warnings']],
                cost_scope='ADMITTED_ONLY_DEFERRED_LIABILITIES_UNPRICED')
        if horizon_pilotage_policy_version is not None:
            summary.update(horizon_pilotage_policy_version=horizon_pilotage_policy_version,
                horizon_pilotage_gap_call_ids=sorted(horizon_gap_certificates),
                horizon_pilotage_gap_unresolved_call_ids=sorted(horizon_gap_unresolved))
        if inventory_risk_policy_version is not None:summary['inventory_risk_v1']=risk_from(tank)
        if inventory_policy_version is not None:
            summary['inventory_policy_version']=inventory_policy_version
            summary['operational_warnings']=deepcopy(tank.get('operational_warnings',[]))
        cp={'window_id':i,'as_of_at':at,'planning_horizon_end':horizon.isoformat(),'freeze_boundary':None,'dynamic_lock_horizon_hours':5,'input_fingerprint':config['source_fingerprint'],'policy_fingerprint':config['policy_fingerprint'],'optimization_configuration':config,'selected_schedule':rows,'completed_calls':[r['call_id'] for r in nextstate['completed_rows']],'protected_commitments_in':protected,'protected_commitments':after['physical_commitments'],'manual_overrides':applied_manual,'lock_state':nextstate['lock_state'],'cargo_progress':nextstate['cargo_progress'],'carried_transfers':nextstate['carried_transfers'],'carried_inventory':nextstate['inventory'],'contract_accounting_state':nextstate['contract_accounting_state'],'in_progress_contract_accounting':nextstate['in_progress_contract_accounting'],'validation_results':{'physical':'VALID' if rows else 'NO_WORK','inventory':'VALID','settlement':'SETTLED' if rows else 'NO_WORK','carryover':'VALID'},'prior_state_fingerprint':_fingerprint(state),'next_state_fingerprint':_fingerprint(nextstate),'next_state':nextstate,'run_fingerprint':manifest['run_fingerprint'],'candidate':candidate,'summary':summary}
        if deferral_policy_version is not None:
            cp.update(deferral_policy_version=deferral_policy_version,due_call_ids=due_call_ids,
                admitted_call_ids=selected,deferred_call_ids=window_deferred,
                deferral_history=deepcopy(previous['deferral_history']),
                admission_attempts=(admission['attempts'] if admission is not None else []))
        if horizon_pilotage_policy_version is not None:
            cp.update(horizon_pilotage_policy_version=horizon_pilotage_policy_version,
                horizon_pilotage_gap_certificates=horizon_gap_certificates,
                horizon_pilotage_gap_unresolved=horizon_gap_unresolved)
        # Re-run independent physical, inventory, settlement and 24h replay before saving.
        try:verified=checked_transition(cp,previous)
        except (ValueError,KeyError,TypeError) as exc:return report('STOPPED_UNRESOLVED',{'code':'INDEPENDENT_REPLAY_FAILED','reason':str(exc)})
        if verified!=nextstate:return report('STOPPED_UNRESOLVED',{'code':'CARRYOVER_MISMATCH'})
        cp['checkpoint_fingerprint']=_fingerprint(cp)
        _write(folder/('window_%03d.json'%i),cp);state=nextstate;results.append(summary);fresh+=1
    return report('REQUESTED_WINDOWS_VALIDATED')
