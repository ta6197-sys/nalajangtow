"""Read-only evidence and coverage classification for V5 Stage-1 scale gates.

This module does not solve, reschedule, change policy, or validate physics itself.
Structural checkpoint validation and independent original-input replay are separate
mandatory inputs. All statements concern a bounded synthetic scenario.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from fractions import Fraction
import hashlib
from pathlib import Path

VERSION = 'V5_STAGE1_FINAL_GATES_EVIDENCE_V1'
GATES = {'S5': (5, 2), 'S10': (10, 3), 'S20': (20, 3),
         'R7': (20, 7), 'R14': (20, 14), 'FULL140': (140, 60)}
TIE_BREAK = 'CANONICAL_ETB_BERTH_V1'


def instant(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Timezone-aware datetime required')
    return result.astimezone(timezone.utc)


def exact(value):
    if value is None or isinstance(value, bool):
        raise ValueError('Exact quantity is unavailable')
    return Fraction(str(value))


def required_window_end(start, windows):
    if type(windows) is not int or not 1 <= windows <= 65:
        raise ValueError('Rolling window count must be 1..65')
    return instant(start) + timedelta(hours=24 * (windows - 1) + 72)


def inspect_coverage(snapshot, config, windows, *, check_declared_berths=True):
    """Fail closed on fixed-flow gaps, report declared per-berth horizon limits.

    Daylight/permission semantics cannot be inferred from timestamps alone.
    Actual pilotage verification remains the integrated solver's responsibility.
    """
    start = instant(config['as_of_at'])
    end = required_window_end(config['as_of_at'], windows)
    blockers, warnings, tank_rows, berth_rows = [], [], [], []
    flow = snapshot.get('fixed_process_flows') or {}
    tanks = snapshot.get('tanks') or []
    segments = flow.get('segments') or []
    if not isinstance(segments, list):
        raise ValueError('Flow segments are invalid')
    try:
        inventory_at = instant(flow['inventory_as_of_at'])
        horizon_end = instant(flow['flow_horizon_end_at'])
    except (KeyError, TypeError, ValueError) as exc:
        inventory_at, horizon_end = None, None
        blockers.append({'code': 'MISSING_FIXED_FLOW_COVERAGE_METADATA', 'detail': str(exc)})
    if inventory_at is not None and inventory_at > start:
        blockers.append({'code': 'START_BEFORE_INITIAL_INVENTORY',
                         'available_start_at': inventory_at.isoformat()})
    if horizon_end is not None and horizon_end < end:
        blockers.append({'code': 'FIXED_FLOW_HORIZON_SHORT',
                         'available_until_at': horizon_end.isoformat(), 'required_until_at': end.isoformat()})
    for tank in tanks:
        tid = tank['tank_id']
        ranges = []
        for seg in segments:
            if seg.get('tank_id') == tid:
                try:
                    ranges.append((instant(seg['start_at']), instant(seg['end_at'])))
                except (KeyError, ValueError, TypeError) as exc:
                    blockers.append({'code': 'INVALID_FIXED_FLOW_SEGMENT', 'tank_id': tid, 'detail': str(exc)})
        ranges.sort()
        cursor = start
        for a, b in ranges:
            if a >= end:
                break
            if b <= start:
                continue
            if b <= a or a > cursor:
                blockers.append({'code': 'FIXED_FLOW_GAP_OR_INVALID_INTERVAL',
                                 'tank_id': tid, 'gap_at': cursor.isoformat()})
                break
            if a < cursor and a >= start:
                blockers.append({'code': 'FIXED_FLOW_OVERLAP', 'tank_id': tid,
                                 'at': a.isoformat()})
                break
            cursor = max(cursor, b)
        if cursor < end:
            blockers.append({'code': 'FIXED_FLOW_INTERVALS_END_EARLY', 'tank_id': tid,
                             'covered_until_at': cursor.isoformat(), 'required_until_at': end.isoformat()})
        tank_rows.append({'tank_id': tid, 'segments': len(ranges),
                          'continuous_through_required_end': cursor >= end})
    if not tanks:
        blockers.append({'code': 'NO_TANKS'})
    declared = (config.get('policy') or {}).get('berths') or {}
    if check_declared_berths:
        for berth in snapshot.get('berths') or []:
            if berth.get('active') != 'Y':
                continue
            bid = berth['berth_id']
            rule = declared.get(bid)
            if not isinstance(rule, dict):
                blockers.append({'code': 'MISSING_ACTIVE_BERTH_POLICY', 'berth_id': bid})
                continue
            row = {'berth_id': bid}
            for field in ('pilotage_windows', 'work_windows'):
                value = rule.get(field)
                if not isinstance(value, list) or not value:
                    warnings.append({'code': 'DECLARED_BERTH_WINDOWS_NOT_CHECKABLE',
                                     'berth_id': bid, 'field': field})
                    row[field] = 'UNKNOWN'
                    continue
                try:
                    latest = max(instant(v['end_at']) for v in value)
                except (KeyError, ValueError, TypeError) as exc:
                    blockers.append({'code': 'INVALID_DECLARED_BERTH_WINDOW',
                                     'berth_id': bid, 'field': field, 'detail': str(exc)})
                    continue
                row[field + '_last_end_at'] = latest.isoformat()
                if latest < end:
                    blockers.append({'code': 'DECLARED_BERTH_WINDOW_HORIZON_SHORT',
                                     'berth_id': bid, 'field': field,
                                     'available_until_at': latest.isoformat(),
                                     'required_until_at': end.isoformat()})
            berth_rows.append(row)
    warnings.append({'code': 'DAYLIGHT_AND_PILOTAGE_SEMANTICS_REQUIRE_REAL_SOLVER_VALIDATION'})
    return {'version': VERSION, 'status': 'BLOCKED' if blockers else 'COVERAGE_PRECHECK_PASS',
            'start_at_utc': start.isoformat(), 'required_until_utc': end.isoformat(),
            'windows': windows, 'flow_horizon_end_utc': horizon_end.isoformat() if horizon_end else None,
            'tank_coverage': tank_rows, 'declared_berth_horizons': berth_rows,
            'blockers': blockers, 'warnings': warnings,
            'interpretation': 'Conservative coverage precheck only; no operational approval'}


def review_stage2b(report, *, source_fingerprint, as_of_at, s5_scope, config=None):
    """Require 60s/120s 20x observed evidence and explicit handling of variance."""
    errors, variations, warnings = [], [], []
    meta = report.get('metadata') or {}
    src = meta.get('source') or {}
    if src.get('source_fingerprint') != source_fingerprint:
        errors.append('STAGE2B_SOURCE_MISMATCH')
    try:
        if instant(src.get('as_of_at', as_of_at)) != instant(as_of_at):
            errors.append('STAGE2B_START_TIME_MISMATCH')
    except (TypeError, ValueError):
        errors.append('STAGE2B_INVALID_START_TIME')
    if config is not None:
        from src.daily_planning.integrated_v3.v5_repeatability import digest
        checks = {
            'policy_hash': digest(config['policy']),
            'pilotage_hash': digest(config['pilotage_context']),
            'settlement_hash': digest({
                'pricing_policy': config['pricing_policy'],
                'clock_policy': config['clock_policy'],
                'settlement_records': config['settlement_records'],
            }),
            'daylight_sha256': hashlib.sha256(
                Path(config['pilotage_context']['daylight_path']).read_bytes()).hexdigest(),
        }
        for field, expected in checks.items():
            if src.get(field) != expected:
                errors.append('STAGE2B_' + field.upper() + '_MISMATCH')
    if sorted(src.get('scope_call_ids') or []) != sorted(s5_scope):
        errors.append('STAGE2B_S5_SCOPE_MISMATCH')
    if meta.get('tie_break_mode') != TIE_BREAK:
        errors.append('STAGE2B_TIE_BREAK_MODE_MISMATCH')
    if meta.get('repetitions') != 20:
        errors.append('STAGE2B_NOT_TWENTY_REPETITIONS')
    if sorted(meta.get('budgets_seconds_per_window') or []) != [60, 120]:
        errors.append('STAGE2B_BUDGETS_NOT_60_AND_120')
    data = report.get('budgets') or {}
    for budget in (60, 120):
        result = data.get(str(budget)) or {}
        if (result.get('expected') != 20 or result.get('valid_and_replayed') != 20
                or result.get('context_consistent') is not True):
            errors.append('STAGE2B_INCOMPLETE_OR_UNREPLAYED_BUDGET_' + str(budget))
        if result.get('status') != 'OBSERVED_REPEATABLE_AT_THIS_BUDGET':
            variations.append({'budget': budget, 'status': result.get('status'),
                               'window_flags': [x.get('diagnostic_flags') for x in result.get('windows') or []]})
        for window in result.get('windows') or []:
            if window.get('warnings'):
                warnings.append({'budget': budget, 'window_id': window.get('window_id'),
                                 'warnings': window['warnings']})
    if report.get('status') != 'COMPLETE_DIAGNOSTIC':
        errors.append('STAGE2B_NOT_COMPLETE_DIAGNOSTIC')
    return {'status': 'BLOCKED_INVALID_EVIDENCE' if errors else
                      'REVIEW_VARIATION_REQUIRED' if variations else 'OBSERVED_REPEATABLE_BOTH_BUDGETS',
            'errors': errors, 'variations': variations, 'proof_warnings': warnings,
            'note': 'Observed repeatability is not full physical/cost global optimality.'}


def audit_gate(report, manifest, checkpoints, evidence_windows, *, gate, scope,
               expected_windows, source_fingerprint, replay_pass, source_calls=None):
    """Separate verified-window success from all-vessel ETU/settlement completion.

    This only evaluates already loaded/fingerprint-checked checkpoints.
    Independent original-source replay MUST have passed separately.
    """
    issues, warnings = [], []
    cfg = (manifest or {}).get('config') or {}
    if report.get('status') != 'REQUESTED_WINDOWS_VALIDATED':
        issues.append('EXECUTION_NOT_FULLY_VALIDATED')
    if report.get('validated_window_count') != expected_windows or len(checkpoints) != expected_windows:
        issues.append('INCOMPLETE_WINDOW_CHAIN')
    if cfg.get('windows') != expected_windows or report.get('requested_windows') != expected_windows:
        issues.append('WINDOW_COUNT_CONTEXT_CHANGED')
    if cfg.get('source_fingerprint') != source_fingerprint or report.get('source_fingerprint') != source_fingerprint:
        issues.append('SOURCE_FINGERPRINT_CHANGED')
    if sorted(cfg.get('scope') or []) != sorted(scope) or sorted(report.get('scope_call_ids') or []) != sorted(scope):
        issues.append('SCOPE_CHANGED')
    if cfg.get('tie_break_mode') != TIE_BREAK:
        issues.append('TIE_BREAK_MODE_CHANGED')
    if not replay_pass:
        issues.append('INDEPENDENT_REPLAY_NOT_PASSED')
    if len(evidence_windows) != len(checkpoints):
        issues.append('READ_WINDOW_EVIDENCE_COUNT_MISMATCH')
    if report.get('operational_approval') is not False or report.get('simulation_only') is not True:
        issues.append('UNSUPPORTED_OPERATIONAL_APPROVAL')

    state = (checkpoints[-1].get('next_state') or {}) if checkpoints else {}
    completed_rows = state.get('completed_rows') or []
    completed_ids = [r.get('call_id') for r in completed_rows if isinstance(r, dict)]
    completed_set, scope_set = set(completed_ids), set(scope)
    if len(completed_ids) != len(completed_set) or not completed_set <= scope_set:
        issues.append('DUPLICATE_OR_OUT_OF_SCOPE_COMPLETION')
    reported_ids = report.get('completed_call_ids')
    if reported_ids is not None:
        if not isinstance(reported_ids, list) or len(reported_ids) != len(completed_set) or set(reported_ids) != completed_set:
            issues.append('REPORTED_COMPLETION_SET_MISMATCH')
    else:
        # execute_scale exposes a completion count; IDs live in the final
        # independently replayed checkpoint, not in execution_report.json.
        listed = checkpoints[-1].get('completed_calls') if checkpoints else None
        if (not isinstance(listed, list) or len(listed) != len(completed_set)
                or set(listed) != completed_set
                or type(report.get('completed_scope_count')) is not int
                or report['completed_scope_count'] != len(completed_set & scope_set)):
            issues.append('REPORTED_COMPLETION_SET_MISMATCH')
    if report.get('all_scope_calls_completed') is not (completed_set == scope_set):
        issues.append('REPORTED_COMPLETION_FLAG_MISMATCH')

    accounting = state.get('contract_accounting_state') or {}
    if not isinstance(accounting, dict):
        accounting = {}
        issues.append('INVALID_ACCOUNTING_STATE')
    settled = {}
    for cid, item in accounting.items():
        cents = item.get('demurrage_cents') if isinstance(item, dict) else None
        entry = item.get('settlement') if isinstance(item, dict) else None
        if cid not in scope_set or type(cents) is not int or cents < 0 or not isinstance(entry, dict) or entry.get('status') != 'SETTLED':
            issues.append('INVALID_COMPLETED_SETTLEMENT')
        else:
            settled[cid] = cents
    if not completed_set <= set(settled):
        issues.append('COMPLETED_CALL_WITHOUT_SETTLEMENT')
    progress = state.get('cargo_progress') or {}
    if not isinstance(progress, dict):
        progress = {}
        issues.append('INVALID_CARGO_PROGRESS')
    completed_qty_issues = []
    completed_time_issues = []
    final_state_at = state.get('at')
    for row in completed_rows:
        try:
            if instant(row['etu_at']) > instant(final_state_at) or instant(row['work_end_at']) > instant(row['etu_at']):
                completed_time_issues.append(row.get('call_id'))
        except (TypeError, ValueError, KeyError):
            completed_time_issues.append(row.get('call_id'))
    if completed_time_issues:
        issues.append('INVALID_COMPLETED_ETU_OR_WORK_END')
    for cid in completed_set:
        item = progress.get(cid)
        if not isinstance(item, dict):
            completed_qty_issues.append(cid)
        else:
            try:
                if (exact(item.get('remaining_bbl')) != 0 or
                        (source_calls is not None and
                         exact(item.get('completed_bbl')) != exact(source_calls[cid]['cargo_volume_bbl']))):
                    completed_qty_issues.append(cid)
            except (ValueError, TypeError):
                completed_qty_issues.append(cid)
    if completed_qty_issues:
        issues.append('COMPLETED_CARGO_REMAINDER_NOT_ZERO')

    for x in evidence_windows:
        if x.get('has_work'):
            if not x.get('first_domain_hash') or not x.get('final_domain_hash'):
                warnings.append({'window_id': x.get('window_id'), 'code': 'MISSING_DOMAIN_EVIDENCE'})
            if not x.get('physical_schedule_hash'):
                warnings.append({'window_id': x.get('window_id'), 'code': 'MISSING_PHYSICAL_EVIDENCE'})
            if x.get('final_domain_optimality_proven') is not True:
                warnings.append({'window_id': x.get('window_id'), 'code': 'FINITE_DOMAIN_OPTIMALITY_NOT_PROVEN'})
    full = completed_set == scope_set and bool(scope_set)
    cumulative = report.get('cumulative_demurrage_cents')
    settled_sum = sum(settled.values()) if set(settled) == scope_set else None
    if full:
        if set(settled) != scope_set:
            issues.append('ALL_CALLS_COMPLETED_BUT_SETTLEMENT_MISSING')
        if type(cumulative) is not int or cumulative < 0:
            issues.append('CUMULATIVE_COST_UNRESOLVED')
        elif settled_sum is not None and cumulative != settled_sum:
            issues.append('CUMULATIVE_COST_MISMATCH')
        if state.get('previous_schedule') or state.get('carried_transfers') or state.get('in_progress_contract_accounting'):
            issues.append('PENDING_STATE_REMAINS_AFTER_FULL_COMPLETION')
        if report.get('unresolved_accounting_call_ids'):
            issues.append('UNRESOLVED_ACCOUNTING_REMAINS')

    # S5 and FULL140 are completion gates; S10/S20/R7/R14 are window-validity diagnostics.
    must_finish = gate in ('S5', 'FULL140')
    if not issues and must_finish and not full:
        issues.append('SCOPE_NOT_FULLY_COMPLETED')
    status = ('BLOCKED_' + issues[0]) if issues else ('PASS_SCOPE_COMPLETE' if full else 'PASS_VALIDATED_WINDOWS_ONLY')
    return {'version': VERSION, 'gate': gate, 'status': status,
            'scope_count': len(scope_set), 'requested_windows': expected_windows,
            'validated_windows': len(checkpoints), 'completed_count': len(completed_set & scope_set),
            'completed_call_ids': sorted(completed_set & scope_set),
            'remaining_call_ids': sorted(scope_set - completed_set),
            'settled_call_count': len(set(settled) & scope_set),
            'cumulative_demurrage_cents': cumulative,
            'exact_settled_sum_cents_if_complete': settled_sum,
            'all_scope_calls_completed': full,
            'independent_replay': 'PASS' if replay_pass else 'FAIL',
            'warnings': warnings, 'issues': issues,
            'per_window': [{'window_id': x.get('window_id'),
                            'cost_cents': x.get('cost_cents'),
                            'solver_status': x.get('solver_status'),
                            'finite_domain_optimality_proven': x.get('final_domain_optimality_proven'),
                            'initial_domain_hash': x.get('first_domain_hash'),
                            'final_domain_hash': x.get('final_domain_hash'),
                            'physical_schedule_hash': x.get('physical_schedule_hash'),
                            'candidate_count_initial': (checkpoints[j].get('summary') or {}).get('initial_candidate_count'),
                            'candidate_count_final': (checkpoints[j].get('summary') or {}).get('final_candidate_count'),
                            'optimization_runtime_seconds': (checkpoints[j].get('summary') or {}).get('optimization_runtime_seconds'),
                            'canonical_selection_status': ((((checkpoints[j].get('candidate') or {}).get('optimizer_result') or {}).get('schedule_tiebreak') or {}).get('status') or 'UNREPORTED')}
                           for j, x in enumerate(evidence_windows)],
            'interpretation': 'Synthetic finite-domain evidence; overlapping window costs NOT additive.'}
