"""Additive V5 Simulation Bridge V2 and V5-native zero-shock parity audit.

This is NOT the legacy Risk Engine and does NOT implement stochastic shocks.
It exports only structurally validated Rolling checkpoints bound to the exact
original source and versioned policy. The zero-shock audit re-evaluates physical
phases, inventory and settlement with existing independent V5 validators; a
complete saved run may additionally be resumed *without solving new windows*.
No source, checkpoint, policy, DB or optimizer decision is mutated.
"""
from __future__ import annotations

from base64 import b64encode, b64decode
from binascii import Error as Base64Error
from copy import deepcopy
from datetime import datetime, timedelta
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

BRIDGE_VERSION = 'V5_SIMULATION_BRIDGE_V2'
AUDIT_VERSION = 'V5_NATIVE_NO_SHOCK_PARITY_V1'


class BridgeError(ValueError):
    """Missing or inconsistent source evidence; never silently repair it."""


def fingerprint(obj: Any) -> str:
    return sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _unique(values, label):
    if not isinstance(values, list) or not all(isinstance(x, str) and x for x in values):
        raise BridgeError('Invalid ' + label)
    if len(values) != len(set(values)):
        raise BridgeError('Duplicate identifiers in ' + label)
    return set(values)


def _time(value):
    try:
        at = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise BridgeError('Invalid timestamp') from exc
    if at.utcoffset() is None:
        raise BridgeError('Timezone-aware timestamp required')
    return at


def initial_rolling_state(start_at: str, *, deferral_enabled: bool) -> dict:
    """Exact initial state from v5_rolling.run_v5_rolling at PR #87."""
    value = {
        'at': _time(start_at).isoformat(), 'inventory': None, 'carried_transfers': [],
        'tank_commitments': [], 'completed_rows': [], 'previous_schedule': [],
        'cargo_progress': {}, 'contract_accounting_state': {},
        'in_progress_contract_accounting': {}, 'lock_state': {}, 'lock_decisions': {},
        'state_origin': 'SIMULATED_PROJECTION_NOT_OBSERVATION',
        'operational_approval': False,
    }
    if deferral_enabled:
        value['deferral_history'] = {}
    return value


def validate_admission(checkpoint: Mapping, calls_by_id: Mapping,
                       *, prior_state: Mapping, config: Mapping) -> dict:
    """Validate and translate due/admitted/deferred with no fabricated reason."""
    rows = checkpoint.get('selected_schedule')
    if not isinstance(rows, list):
        raise BridgeError('Missing selected schedule')
    selected = _unique([r.get('call_id') for r in rows if isinstance(r, dict)], 'schedule')
    if len(selected) != len(rows):
        raise BridgeError('Invalid schedule rows')
    if not selected <= set(calls_by_id):
        raise BridgeError('Scheduled call outside verified source')
    version = config.get('deferral_policy_version')
    if version is None:
        if checkpoint.get('deferred_call_ids'):
            raise BridgeError('Unexpected deferrals in legacy checkpoint')
        return {'version': None, 'due_call_ids': sorted(selected),
                'admitted_call_ids': sorted(selected), 'deferred_call_ids': [],
                'by_call': {}, 'admission_attempts': []}
    due = _unique(checkpoint.get('due_call_ids'), 'due_call_ids')
    admitted = _unique(checkpoint.get('admitted_call_ids'), 'admitted_call_ids')
    deferred = _unique(checkpoint.get('deferred_call_ids'), 'deferred_call_ids')
    if due != admitted | deferred or admitted & deferred or admitted != selected:
        raise BridgeError('Incomplete or overlapping admission partition')
    if not due <= set(calls_by_id):
        raise BridgeError('Admission call outside verified source')
    protected = {r['call_id'] for r in checkpoint.get('protected_commitments_in', [])}
    previous_scheduled = {r['call_id'] for r in prior_state.get('previous_schedule', [])}
    if deferred & (protected | previous_scheduled):
        raise BridgeError('Protected or previously scheduled call deferred')
    if any(calls_by_id[cid].get('fixed_etb_is_hard') is not False for cid in deferred):
        raise BridgeError('FIX or unknown-hardness call deferred')
    history = checkpoint.get('deferral_history')
    if not isinstance(history, dict):
        raise BridgeError('Deferral history missing')
    certificates = checkpoint.get('horizon_pilotage_gap_certificates', {})
    if not isinstance(certificates, dict) or not set(certificates) <= deferred:
        raise BridgeError('Pilotage certificates must refer to deferred calls')
    if config.get('horizon_pilotage_policy_version') is None and certificates:
        raise BridgeError('Unexpected pilotage-gap certificate')
    prior_history = prior_state.get('deferral_history', {})
    # Authoritative source-based count/wait rules; no reinvention of their math.
    from .v5_deferral import verify_history
    try:
        verified = verify_history(calls_by_id, prior_history, history,
            at=checkpoint['as_of_at'], due=due, admitted=admitted,
            deferred=deferred, protected=protected, previous_schedule=previous_scheduled)
    except (KeyError, TypeError, ValueError) as exc:
        raise BridgeError('Source-based deferral history replay failed: ' + str(exc)) from exc
    if verified != history:
        raise BridgeError('Deferral history is not replayable')
    by_call = {}
    for cid in sorted(due):
        record = deepcopy(history[cid])
        if cid in deferred:
            if cid in certificates:
                reason = 'PROVEN_NO_CURRENT_HORIZON_INBOUND_PILOTAGE_SLOT'
                record['pilotage_certificate'] = deepcopy(certificates[cid])
            else:
                # Stage 2B does not store a definitive per-call physical cause.
                reason = 'STAGE2B_SAFE_ADMISSION_DEFERRED_CAUSE_NOT_PROVEN'
        else:
            reason = 'ADMITTED'
        record['decision_reason_class'] = reason
        record['contract_liability'] = ('UNPRICED_NOT_ZERO' if cid in deferred else 'SEE_SETTLEMENT_RECORD')
        by_call[cid] = record
    return {'version': version, 'due_call_ids': sorted(due),
            'admitted_call_ids': sorted(admitted), 'deferred_call_ids': sorted(deferred),
            'by_call': by_call, 'admission_attempts': deepcopy(checkpoint.get('admission_attempts', [])),
            'pilotage_unresolved': deepcopy(checkpoint.get('horizon_pilotage_gap_unresolved', {}))}


def vessel_statuses(snapshot: Mapping, checkpoint: Mapping, scope: set[str]) -> list[dict]:
    """Synthetic projection at T+24, not real-time/ATA observation."""
    now = _time(checkpoint['next_state']['at'])
    state = checkpoint['next_state']
    completed = {r['call_id'] for r in state.get('completed_rows', [])}
    ongoing = {r['call_id'] for r in state.get('previous_schedule', [])
               if _time(r['berthing_start_at']) <= now}
    calls = {c['call_id']: c for c in snapshot['calls'] if c['call_id'] in scope}
    due_history = state.get('deferral_history', {})
    out = []
    for cid in sorted(scope):
        c = calls[cid]
        if cid in completed:
            status = 'COMPLETED'
        elif cid in ongoing:
            status = 'IN_PROGRESS_SIMULATED'
        elif _time(c['eta_at']) <= now:
            status = 'WAITING_ETA_BASED_NOT_OBSERVED'
        else:
            status = 'FUTURE_ETA'
        history = due_history.get(cid)
        out.append({
            'call_id': cid, 'vessel_id': c['vessel_id'], 'eta_at': c['eta_at'],
            'operational_status': status,
            'deferral_decision': history.get('status') if history else 'NOT_RECORDED',
            'cargo_progress': deepcopy(state.get('cargo_progress', {}).get(cid)),
            'contract_accounting': deepcopy(state.get('contract_accounting_state', {}).get(cid)),
            'in_progress_accounting': deepcopy(state.get('in_progress_contract_accounting', {}).get(cid)),
            'status_origin': 'SIMULATED_PROJECTION_NOT_OBSERVATION',
        })
    return out


def _source_contracts(snapshot: Mapping, scope: set[str]) -> dict:
    contract_rows = (snapshot.get('workbook_tables') or {}).get('contracts', {}).get('rows')
    if not isinstance(contract_rows, list):
        raise BridgeError('Verified contract table missing')
    mapped = {}
    for row in contract_rows:
        if not isinstance(row, dict):
            raise BridgeError('Invalid contract row')
        cid = row.get('case_id', row.get('call_id'))
        if cid in scope:
            if cid in mapped:
                raise BridgeError('Duplicate contract evidence for ' + cid)
            mapped[cid] = deepcopy(row)
    # Leave missing contracts explicitly unresolved instead of inventing terms.
    return {'by_call': mapped, 'missing_contract_call_ids': sorted(scope-set(mapped))}


def build_bundle(snapshot: Mapping, runtime_config: Mapping,
                 manifest: Mapping, checkpoints: list[Mapping], daylight_raw: bytes) -> dict:
    """Create self-contained, read-only simulation baseline material from V5."""
    from .v5_stage1_contract import build_window_descriptor
    if not isinstance(snapshot, dict) or not isinstance(manifest, dict):
        raise BridgeError('Verified source and manifest required')
    conf = manifest.get('config')
    if not isinstance(conf, dict) or not checkpoints:
        raise BridgeError('At least one validated checkpoint required')
    if fingerprint(snapshot) != conf.get('source_fingerprint'):
        raise BridgeError('Source fingerprint mismatch')
    raw_sha = sha256(daylight_raw).hexdigest()
    context = runtime_config.get('pilotage_context')
    if not isinstance(context, dict):
        raise BridgeError('Pilotage context missing')
    expected_policy = fingerprint({
        'policy': runtime_config['policy'], 'pilotage_context': context,
        'daylight_sha256': raw_sha, 'pricing_policy': runtime_config['pricing_policy'],
        'clock_policy': runtime_config['clock_policy'],
        'settlement_records': runtime_config['settlement_records'],
    })
    if expected_policy != conf.get('policy_fingerprint'):
        raise BridgeError('Policy/daylight/pricing fingerprint mismatch')
    if fingerprint(conf) != manifest.get('run_fingerprint'):
        raise BridgeError('Rolling manifest fingerprint mismatch')
    scope = _unique(conf.get('scope'), 'manifest scope')
    if not scope <= {c['call_id'] for c in snapshot.get('calls', [])}:
        raise BridgeError('Manifest scope not in verified source')
    callmap = {c['call_id']: c for c in snapshot['calls']}
    contracts = _source_contracts(snapshot, scope)
    if contracts['missing_contract_call_ids']:
        raise BridgeError('Missing source contract records: '+str(contracts['missing_contract_call_ids']))
    initial = initial_rolling_state(conf['start_at'], deferral_enabled=conf.get('deferral_policy_version') is not None)
    windows = []
    for index, cp in enumerate(checkpoints, 1):
        if cp.get('window_id') != index:
            raise BridgeError('Noncontiguous checkpoints')
        prior = initial if index == 1 else checkpoints[index-2]['next_state']
        if cp.get('prior_state_fingerprint') != fingerprint(prior):
            raise BridgeError('Prior rolling state does not match checkpoint')
        from .v5_stage1_contract import _validated_checkpoint
        _validated_checkpoint(manifest, cp)
        if cp.get('next_state_fingerprint') != fingerprint(cp.get('next_state')):
            raise BridgeError('Next rolling state fingerprint mismatch')
        admission = validate_admission(cp, callmap, prior_state=prior, config=conf)
        v1 = build_window_descriptor(manifest, cp,
                         prior_state=(prior if index > 1 else None))
        windows.append({
            'window_id': index, 'as_of_at': cp['as_of_at'],
            'source_checkpoint_fingerprint': cp['checkpoint_fingerprint'],
            'prior_state_at_start': deepcopy(prior),
            'prior_state_origin': ('ENGINE_INITIAL_STATE_FINGERPRINT_MATCH' if index==1 else 'PREVIOUS_VALIDATED_CHECKPOINT'),
            'baseline_descriptor_v1': v1,
            'admission': admission,
            'pilotage_gap_certificates': deepcopy(cp.get('horizon_pilotage_gap_certificates', {})),
            'state_at_boundary': vessel_statuses(snapshot, cp, scope),
            'source_derived_contracts': {cid: contracts['by_call'].get(cid)
                for cid in admission['due_call_ids']},
            'no_shock_replay_status': 'NOT_RUN',
        })
    bundle = {
        'schema_version': BRIDGE_VERSION,
        'export_status': 'BOUND_SOURCE_AND_STRUCTURAL_CHECKPOINTS_NOT_SIMULATION_REPLAY',
        'simulation_only': True, 'operational_approval': False,
        'legacy_risk_engine_connected': False,
        'source_fingerprint': conf['source_fingerprint'],
        'policy_fingerprint': conf['policy_fingerprint'],
        'run_fingerprint': manifest['run_fingerprint'],
        'requested_windows': conf['windows'], 'validated_windows': len(windows),
        'all_requested_windows_validated': len(windows)==conf['windows'],
        'full_period_global_optimality_proven': False,
        'cost_scope': 'WINDOW_PROJECTED_COSTS_OVERLAP_NOT_ADDITIVE',
        'source_snapshot': deepcopy(snapshot),
        'operating_policy': deepcopy(runtime_config['policy']),
        'pilotage_context': deepcopy(context),
        'daylight_source_sha256': raw_sha,
        'daylight_source_base64': b64encode(daylight_raw).decode('ascii'),
        'settlement_records': deepcopy(runtime_config['settlement_records']),
        'pricing_policy': runtime_config['pricing_policy'],
        'clock_policy': runtime_config['clock_policy'],
        'rolling_manifest': deepcopy(manifest),
        'source_contract_scope': contracts,
        'windows': windows,
    }
    verify_bundle_attestation(bundle)
    return bundle


def verify_bundle_attestation(bundle: Mapping) -> dict:
    """Read-only portable integrity and cross-window checks; NOT solver replay."""
    if not isinstance(bundle, dict) or bundle.get('schema_version') != BRIDGE_VERSION:
        raise BridgeError('Wrong Bridge V2 version')
    if bundle.get('operational_approval') is not False or bundle.get('simulation_only') is not True:
        raise BridgeError('Bridge is not an operationally approved result')
    manifest = bundle.get('rolling_manifest') or {}
    config = manifest.get('config') or {}
    if fingerprint(config) != manifest.get('run_fingerprint') or \
       manifest.get('run_fingerprint') != bundle.get('run_fingerprint'):
        raise BridgeError('Manifest attestation mismatch')
    if fingerprint(bundle.get('source_snapshot')) != config.get('source_fingerprint') or \
       bundle.get('source_fingerprint') != config.get('source_fingerprint'):
        raise BridgeError('Source attestation mismatch')
    try:
        raw = b64decode(bundle['daylight_source_base64'], validate=True)
    except (ValueError, KeyError, Base64Error) as exc:
        raise BridgeError('Missing or malformed daylight source') from exc
    if sha256(raw).hexdigest() != bundle.get('daylight_source_sha256'):
        raise BridgeError('Daylight source digest mismatch')
    if fingerprint({'policy':bundle['operating_policy'],
       'pilotage_context':bundle['pilotage_context'],
       'daylight_sha256':bundle['daylight_source_sha256'],
       'pricing_policy':bundle['pricing_policy'],
       'clock_policy':bundle['clock_policy'],
       'settlement_records':bundle['settlement_records']}) != config.get('policy_fingerprint'):
        raise BridgeError('Policy attestation mismatch')
    windows = bundle.get('windows')
    if not isinstance(windows, list) or not windows or len(windows)>config.get('windows',0):
        raise BridgeError('Missing or invalid validated windows')
    scope = _unique(config.get('scope'), 'manifest scope')
    for i, w in enumerate(windows, 1):
        if w.get('window_id') != i or w.get('baseline_descriptor_v1',{}).get('run_fingerprint') != bundle['run_fingerprint']:
            raise BridgeError('Window ordering or descriptor attestation mismatch')
        descriptor = w['baseline_descriptor_v1']
        prior = w.get('prior_state_at_start')
        if not isinstance(prior,dict) or fingerprint(prior)!=descriptor.get('prior_state_fingerprint',fingerprint(prior)):
            raise BridgeError('Missing or inconsistent prior state')
        if i==1:
            exact_initial=initial_rolling_state(config['start_at'],deferral_enabled=config.get('deferral_policy_version') is not None)
            if prior != exact_initial:
                raise BridgeError('First-window prior state mismatch')
        elif prior!=windows[i-2]['baseline_descriptor_v1']['next_state']:
            raise BridgeError('Cross-window prior state mismatch')
        selected = _unique([r['call_id'] for r in descriptor['selected_schedule']], 'window schedule')
        a = w.get('admission') or {}
        due = _unique(a.get('due_call_ids'), 'due')
        admitted = _unique(a.get('admitted_call_ids'), 'admitted')
        deferred = _unique(a.get('deferred_call_ids'), 'deferred')
        if admitted!=selected or admitted&deferred or due!=admitted|deferred or not due<=scope:
            raise BridgeError('Tampered or incomplete window admission')
        if set(w.get('pilotage_gap_certificates',{})) - deferred:
            raise BridgeError('Pilotage certificate without deferral')
        statuses = w.get('state_at_boundary')
        expected_statuses = vessel_statuses(bundle['source_snapshot'],
              {'next_state':descriptor['next_state']},scope)
        if statuses!=expected_statuses:
            raise BridgeError('Vessel status projection differs from source and checkpoint')
        if not isinstance(statuses, list) or {c['call_id'] for c in statuses}!=scope or len(statuses)!=len(scope):
            raise BridgeError('Missing or duplicated vessel state')
        if any(v.get('status_origin')!='SIMULATED_PROJECTION_NOT_OBSERVATION' for v in statuses):
            raise BridgeError('False real-time vessel status')
        if i<len(windows) and descriptor['next_state']!=windows[i]['prior_state_at_start']:
            raise BridgeError('Cross-window next/prior-state mismatch')
    if bool(bundle.get('all_requested_windows_validated')) != (len(windows)==config['windows']):
        raise BridgeError('False requested-windows completion')
    return {'status':'STRUCTURAL_ATTESTATION_PASS','validated_windows':len(windows),
            'full_native_no_shock_replay':'NOT_RUN',
            'operational_approval':False}


def replay_window_no_shock(snapshot, runtime_config, checkpoint, prior_state) -> dict:
    """Independent phase/inventory/pricing replay for one validated window.

    Does not optimize new ETBs or treat deferred liabilities as paid. The old
    V5 engine/validators are reused; this is NOT a separate Risk simulator.
    """
    from ..integrated_input.v5 import eligible_berth_ids
    from ..physical_scheduling.validator import validate_result
    from ..tank.v5_inventory import validate_v5_inventory
    from .v5_inventory_policy import policy_view
    from .v5_physical import prepare_pilotage_policy
    from .coordinator import validate_v5_candidate
    from .v5_settlement import price_v5_schedule
    from .v5_inventory_risk import classify_inventory_points
    from .v5_deferral import verify_history
    from .v5_rolling import _empty_tank
    at = checkpoint['as_of_at']
    rows = checkpoint['selected_schedule']
    work = policy_view(snapshot, runtime_config.get('inventory_policy_version'))
    protected = checkpoint['protected_commitments_in']
    protected_ids = {r['call_id'] for r in protected}
    previous = deepcopy(prior_state)
    previous['tank_commitments'] = [d for d in previous['tank_commitments'] if d['call_id'] in protected_ids]
    if rows:
        ids=[r['call_id'] for r in rows]
        calls={c['call_id']:c for c in work['calls']}
        eligible={cid:eligible_berth_ids(calls[cid],work['berths'],work['tanks']) for cid in ids}
        from ..integrated_input.validator import timestamp
        history_start=min((r['pob_at'] for r in protected),key=timestamp,default=None)
        applied,issue=prepare_pilotage_policy(work,ids,eligible,runtime_config['policy'],
                   runtime_config['pilotage_context'],at,{},history_start)
        if issue:
            raise BridgeError('NO_SHOCK pilotage not validated: '+str(issue))
        physical=validate_result(work, rows, applied)
        if physical['status']!='VALID':
            raise BridgeError('NO_SHOCK physical mismatch: '+str(physical['issues']))
        candidate=checkpoint['candidate']
        tank0=candidate['tank_result']
        projection=tank0.get('projection_end_at')
        replay=validate_v5_candidate(work,rows,as_of_at=at,
                    projection_end_at=projection,
                    initial_inventory=previous.get('inventory'),
                    carried_transfers=previous.get('carried_transfers'),
                    tank_commitments=previous.get('tank_commitments'),
                    same_tank_policy='ALLOW',physical_commitments=protected)
        if replay['status']!='INVENTORY_VALID':
            raise BridgeError('NO_SHOCK inventory not validated: '+str(replay['issues']))
        tank=replay['tank_result']
        for name in ('inventories','inventory_extrema','external_net_flow_totals_bbl',
                     'transfer_definitions','carried_inventory_bbl','carried_transfers'):
            if tank.get(name)!=tank0.get(name):
                raise BridgeError('NO_SHOCK inventory mismatch: '+name)
        price=price_v5_schedule(work,rows,as_of_at=at,
                settlement_records=runtime_config['settlement_records'],
                pricing_policy=runtime_config['pricing_policy'],
                clock_policy=runtime_config['clock_policy'])
        if price['status']!='SETTLED' or price['total_demurrage_cents']!=candidate['total_demurrage_cents'] or \
                price['vessel_demurrage_breakdown']!=candidate['vessel_demurrage_breakdown']:
            raise BridgeError('NO_SHOCK contract settlement/cents mismatch')
        total=price['total_demurrage_cents']
    else:
        if checkpoint['candidate'] is not None:
            raise BridgeError('NO_WORK window claims an optimizer candidate')
        tank=_empty_tank(work,at,previous)
        total=0
    if tank['carried_inventory_bbl']!=checkpoint['next_state']['inventory'] or \
            tank['carried_transfers']!=checkpoint['next_state']['carried_transfers']:
        raise BridgeError('NO_SHOCK next-window inventory/transfer mismatch')
    if runtime_config.get('inventory_risk_policy_version') is not None:
        risk=classify_inventory_points(work['tanks'],tank['inventories'],
                                        version=runtime_config['inventory_risk_policy_version'])
        if risk!=checkpoint['summary'].get('inventory_risk_v1'):
            raise BridgeError('NO_SHOCK risk classification mismatch')
    return {'window_id':checkpoint['window_id'],
            'status':'PHYSICAL_INVENTORY_SETTLEMENT_PARITY_PASS',
            'schedule_row_count':len(rows),'demurrage_cents':total,
            'tank_event_series_match':True,'carryover_inventory_match':True,
            'physical_times_match':True,'contract_settlement_exact_cents_match':True,
            'no_new_optimization':True}


def verify_run_no_shock(snapshot, runtime_config, manifest, checkpoints,
                        *, run_directory, require_complete=True) -> dict:
    """Read-only native V5 no-shock replay; FAIL CLOSED on absent evidence.

    A complete saved run is required before the existing replay-only resume is
    invoked, so it cannot create additional windows. Partial runs can have
    per-window parity but never a complete baseline certification.
    """
    from .v5_rolling import run_v5_rolling
    config=manifest['config']
    if require_complete and len(checkpoints)!=config['windows']:
        raise BridgeError('Incomplete run: cannot certify complete NO_SHOCK baseline')
    previous=initial_rolling_state(config['start_at'],
                 deferral_enabled=config.get('deferral_policy_version') is not None)
    results=[]
    for cp in checkpoints:
        if cp.get('prior_state_fingerprint')!=fingerprint(previous):
            raise BridgeError('NO_SHOCK prior state fingerprint mismatch')
        results.append(replay_window_no_shock(snapshot,runtime_config,cp,previous))
        previous=cp['next_state']
    complete=len(checkpoints)==config['windows']
    if not complete:
        return {'audit_version':AUDIT_VERSION,'status':'PARTIAL_WINDOWS_ONLY_NOT_FULL_BASELINE',
                'validated_windows':len(checkpoints),'window_parity':results,
                'native_resume_replay':'NOT_RUN_INCOMPLETE','independent_shock_engine':'NOT_IMPLEMENTED',
                'operational_approval':False}
    # Native checkpoint-only resume replays all 24h state transitions with no
    # optimizer call: no saved checkpoint is overwritten and no new is created.
    rerun=run_v5_rolling(snapshot,data_dir=runtime_config['data_dir'],
         policy=runtime_config['policy'],pilotage_context=runtime_config['pilotage_context'],
         as_of_at=runtime_config['as_of_at'],output_dir=Path(run_directory),
         call_ids=config['scope'],windows=config['windows'],
         time_limit_seconds=config['time_limit_seconds'],
         max_cost_candidates=config['max_cost_candidates'],
         pricing_policy=runtime_config['pricing_policy'],
         clock_policy=runtime_config['clock_policy'],
         settlement_records=runtime_config['settlement_records'],
         approved_commitments=config.get('approved_commitments',[]),
         override_events=config.get('override_events',{}),resume=True,
         rolling_optimizer_mode=config['rolling_optimizer_mode'],
         max_improvement_rounds=config['max_improvement_rounds'],
         max_candidates_per_pair=config['max_candidates_per_pair'],
         max_total_candidates=config['max_total_candidates'],
         **({'tie_break_mode':config['tie_break_mode']} if config.get('tie_break_mode','NONE')!='NONE' else {}),
         **({'inventory_policy_version':runtime_config['inventory_policy_version']} if runtime_config.get('inventory_policy_version') else {}),
         **({'inventory_risk_policy_version':runtime_config['inventory_risk_policy_version']} if runtime_config.get('inventory_risk_policy_version') else {}),
         **({'deferral_policy_version':runtime_config['deferral_policy_version'],
             'max_deferral_probes':runtime_config['max_deferral_probes']} if runtime_config.get('deferral_policy_version') else {}),
         **({'horizon_pilotage_policy_version':runtime_config['horizon_pilotage_policy_version']} if runtime_config.get('horizon_pilotage_policy_version') else {}))
    if rerun.get('status')!='REQUESTED_WINDOWS_VALIDATED' or rerun.get('validated_window_count')!=len(checkpoints) or \
       rerun.get('rolling_run_id')!=manifest['run_fingerprint']:
        raise BridgeError('NO_SHOCK full native replay failed: '+str(rerun.get('stopping_reason')))
    return {'audit_version':AUDIT_VERSION,'status':'NATIVE_NO_SHOCK_BASELINE_PARITY_PASS',
            'validated_windows':len(checkpoints),'window_parity':results,
            'native_resume_replay':'PASS',
            'scope_all_calls_completed':set(config['scope'])<=set(rerun.get('completed_call_ids',[])),
            'independent_shock_engine':'NOT_IMPLEMENTED',
            'simulation_only':True,'operational_approval':False}
