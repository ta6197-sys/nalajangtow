"""Read-only Stage 2C S5 completion/contract-coverage audit.

This does NOT replace Stage-4 independent physics, inventory, settlement and
state-transition replay. Pass `replay_status='PASS'` only after verify_resume.py
has successfully checked the exact persisted run. No optimizer is modified.
"""
from __future__ import annotations

from datetime import datetime
from fractions import Fraction

VERSION = 'V5_STAGE2C_S5_COMPLETION_AUDIT_V1'


def _exact(value):
    if value is None or isinstance(value, bool):
        raise ValueError('Missing exact quantity')
    return Fraction(str(value))


def _instant(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Timezone-aware date-time required')
    return result


def audit_s5_completion(report, manifest, checkpoints, *, source_calls,
                        replay_status, expected_source_fingerprint,
                        expected_policy_fingerprint, expected_start_at,
                        expected_windows, expected_tie_break_mode):
    """Audit final 24h carry-forward state, not overlapping 72h projections.

    All scope vessel IDs are taken from the persisted report and crosschecked
    against the authoritative source snapshot. Does not infer evidence.
    """
    issues = []
    def issue(code, **details):
        issues.append({'code': code, **details})

    cfg = (manifest or {}).get('config') or {}
    scope = report.get('scope_call_ids')
    if (not isinstance(scope, list) or len(scope) != 5 or
            len(set(scope)) != 5 or not set(scope) <= set(source_calls)):
        issue('INVALID_S5_SCOPE')
        scope = scope if isinstance(scope, list) else []
    scope_ids = set(scope)
    if cfg.get('scope') != sorted(scope):
        issue('MANIFEST_SCOPE_MISMATCH')
    if report.get('source_fingerprint') != expected_source_fingerprint or cfg.get('source_fingerprint') != expected_source_fingerprint:
        issue('SOURCE_FINGERPRINT_MISMATCH')
    if cfg.get('policy_fingerprint') != expected_policy_fingerprint:
        issue('POLICY_FINGERPRINT_MISMATCH')
    if cfg.get('start_at') != expected_start_at:
        issue('START_TIME_MISMATCH')
    if cfg.get('windows') != expected_windows or report.get('requested_windows') != expected_windows:
        issue('WINDOW_CONFIGURATION_MISMATCH')
    if cfg.get('rolling_optimizer_mode') != 'INTEGRATED_COST_AWARE_V1':
        issue('WRONG_ROLLING_MODE')
    if cfg.get('objective_mode') != 'MIN_TOTAL_DEMURRAGE' or cfg.get('inventory_constraint_mode') != 'EXACT_EVENT_BALANCE_V1':
        issue('WRONG_OPTIMIZER_CONFIGURATION')
    if cfg.get('tie_break_mode', 'NONE') != expected_tie_break_mode:
        issue('TIE_BREAK_MODE_MISMATCH')
    if report.get('simulation_only') is not True or report.get('operational_approval') is not False:
        issue('UNSUPPORTED_OPERATIONAL_APPROVAL')

    valid_chain = (report.get('status') == 'REQUESTED_WINDOWS_VALIDATED'
                   and len(checkpoints) == expected_windows
                   and report.get('validated_window_count') == expected_windows)
    if not valid_chain:
        issue('INCOMPLETE_OR_UNVALIDATED_WINDOW_CHAIN', observed_windows=len(checkpoints),
              execution_status=report.get('status'))
    if valid_chain and replay_status != 'PASS':
        issue('INDEPENDENT_REPLAY_NOT_PASSED', replay_status=replay_status)
    if not checkpoints:
        issue('NO_CHECKPOINTS')

    final_state = (checkpoints[-1].get('next_state') or {}) if checkpoints else {}
    completed_rows = final_state.get('completed_rows') or []
    completed_ids = [r.get('call_id') for r in completed_rows if isinstance(r, dict)]
    completed_set = set(completed_ids)
    if len(completed_set) != len(completed_ids):
        issue('DUPLICATED_COMPLETED_CALL')
    if completed_set - scope_ids:
        issue('OUT_OF_SCOPE_COMPLETED_CALLS', call_ids=sorted(completed_set - scope_ids))
    if checkpoints and checkpoints[-1].get('completed_calls') != sorted(completed_set):
        # The original code writes completed calls in source-row order, which is
        # not guaranteed to be lexical, so compare as sets after list validation.
        listed = checkpoints[-1].get('completed_calls')
        if not isinstance(listed, list) or len(listed) != len(completed_set) or set(listed) != completed_set:
            issue('CHECKPOINT_COMPLETED_SET_MISMATCH')
    if report.get('completed_scope_count') != len(completed_set & scope_ids):
        issue('REPORTED_COMPLETED_COUNT_MISMATCH')
    if report.get('all_scope_calls_completed') is not (scope_ids == completed_set):
        issue('REPORTED_COMPLETION_FLAG_MISMATCH')
    for row in completed_rows:
        if not isinstance(row, dict) or row.get('call_id') not in scope_ids:
            continue
        try:
            if _instant(row['etu_at']) > _instant(final_state['at']):
                issue('ETU_AFTER_COMMITTED_STATE', call_id=row['call_id'])
            if _instant(row['work_end_at']) > _instant(row['etu_at']):
                issue('ETU_BEFORE_WORK_END', call_id=row['call_id'])
        except (KeyError, TypeError, ValueError) as exc:
            issue('INVALID_COMPLETED_TIME', call_id=row.get('call_id'), reason=str(exc))

    settled = final_state.get('contract_accounting_state') or {}
    if not isinstance(settled, dict):
        issue('INVALID_ACCOUNTING_STATE')
        settled = {}
    account_ids = set(settled)
    if account_ids - scope_ids:
        issue('EXTRA_ACCOUNTING_IDS', call_ids=sorted(account_ids - scope_ids))
    amounts = {}
    for cid, record in settled.items():
        if not isinstance(record, dict):
            issue('INVALID_SETTLED_RECORD', call_id=cid)
            continue
        cents = record.get('demurrage_cents')
        if type(cents) is not int or cents < 0 or not isinstance(record.get('settlement'), dict) or record['settlement'].get('status') != 'SETTLED':
            issue('INVALID_SETTLED_RECORD', call_id=cid)
        else:
            amounts[cid] = cents
    progress = final_state.get('cargo_progress') or {}
    if not isinstance(progress, dict):
        issue('INVALID_CARGO_PROGRESS')
        progress = {}
    if completed_set == scope_ids and scope_ids:
        if account_ids != scope_ids:
            issue('MISSING_FINAL_SETTLEMENT', missing=sorted(scope_ids - account_ids))
        if final_state.get('in_progress_contract_accounting'):
            issue('IN_PROGRESS_ACCOUNTING_REMAINS')
        if final_state.get('previous_schedule'):
            issue('PENDING_SCHEDULE_REMAINS')
        if final_state.get('carried_transfers'):
            issue('CARRIED_TRANSFERS_REMAIN')
        for cid in sorted(scope_ids):
            try:
                actual = progress[cid]
                expected = _exact(source_calls[cid]['cargo_volume_bbl'])
                if _exact(actual['completed_bbl']) != expected or _exact(actual['remaining_bbl']) != 0:
                    issue('CARGO_NOT_FULLY_RECONCILED', call_id=cid)
            except (KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
                issue('CARGO_ACCOUNTING_UNRESOLVED', call_id=cid, reason=str(exc))
        if report.get('unresolved_accounting_call_ids'):
            issue('UNRESOLVED_ACCOUNTING_LIST', call_ids=report['unresolved_accounting_call_ids'])
        total = report.get('cumulative_demurrage_cents')
        if type(total) is not int or total < 0:
            issue('CUMULATIVE_COST_UNRESOLVED')
        elif set(amounts) == scope_ids and total != sum(amounts.values()):
            issue('CUMULATIVE_COST_MISMATCH', observed=total, exact_settled_sum=sum(amounts.values()))

    hard_fail = bool(issues)
    if hard_fail:
        status = 'BLOCKED_INVALID_OR_UNVERIFIED_EVIDENCE'
    elif completed_set != scope_ids:
        status = 'VALIDATED_WINDOWS_SCOPE_INCOMPLETE'
    else:
        status = 'PASS_S5_ALL_FIVE_COMPLETED_SETTLED_AND_REPLAYED'
    return {'version': VERSION, 'status': status, 'simulation_only': True,
            'operational_approval': False, 'scope': 'S5_SOURCE_VERIFIED_5_CALLS_ONLY',
            'requested_windows': expected_windows, 'observed_windows': len(checkpoints),
            'window_chain_validated': valid_chain, 'independent_replay': replay_status,
            'source_fingerprint': expected_source_fingerprint,
            'policy_fingerprint': expected_policy_fingerprint,
            'tie_break_mode': expected_tie_break_mode,
            'scope_call_ids': sorted(scope_ids),
            'completed_call_ids': sorted(completed_set & scope_ids),
            'remaining_call_ids': sorted(scope_ids - completed_set),
            'completed_count': len(completed_set & scope_ids),
            'settled_call_ids': sorted(account_ids & scope_ids),
            'final_cumulative_demurrage_cents': report.get('cumulative_demurrage_cents'),
            'settled_cents_sum': sum(amounts.values()) if set(amounts) == scope_ids else None,
            'issues': issues,
            'limitations': [
                'Only five selected source calls; other 135 source calls excluded.',
                'Contract amounts under synthetic pricing/policy are not operational invoices.',
                'Overlapping 72h window projected costs must not be summed.',
                'No continuous-time or full-140 global optimality proof.',
                'Independent physics, tank and settlement are checked by separate Stage-4 verify_resume.py.',
            ]}
