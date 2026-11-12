"""Read-only V5 repeatability comparison, independent of optimization decisions.

This is a diagnostic/aggregation layer, not a physical, inventory or financial
validator. Checkpoints must first pass the repository's load_checkpoints().
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

VERSION = 'V5_REPEATABILITY_AUDIT_V1'


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def iso_utc(text: str) -> str:
    value = datetime.fromisoformat(text)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('ETB must be timezone-aware')
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


def read_window(cp: dict) -> dict:
    """Extract actual Stage-4 checkpoint fields. Never infer missing evidence."""
    summary = cp.get('summary') or {}
    candidate = cp.get('candidate') or {}
    optimizer = candidate.get('optimizer_result') or {}
    history = optimizer.get('cost_improvement_history') or []
    first_hash = history[0].get('candidate_domain_fingerprint') if history else None
    final_domain = optimizer.get('final_candidate_domain') or {}
    final_hash = final_domain.get('candidate_domain_fingerprint') or optimizer.get('candidate_domain_fingerprint')
    rows = cp.get('selected_schedule') or []
    schedule = {}
    physical_schedule = {}
    physical_fields = ('hose_connection_start_at', 'hose_connection_end_at',
                       'work_intervals', 'work_start_at', 'work_end_at', 'etu_at')
    missing_physical = []
    for row in rows:
        cid = row['call_id']
        if cid in schedule:
            raise ValueError('Duplicate call in checkpoint window: ' + cid)
        schedule[cid] = {'berth_id': row['berth_id'], 'etb_utc': iso_utc(row['etb_at'])}
        missing = [key for key in physical_fields if row.get(key) is None]
        if missing:
            missing_physical.append({'call_id': cid, 'fields': missing})
            continue
        intervals = row['work_intervals']
        if not isinstance(intervals, list) or not intervals or any(not isinstance(x, (list, tuple)) or len(x) != 2 for x in intervals):
            missing_physical.append({'call_id': cid, 'fields': ['work_intervals']})
            continue
        physical_schedule[cid] = {'berth_id': row['berth_id'], 'etb_at': iso_utc(row['etb_at']),
            **{key: iso_utc(row[key]) for key in physical_fields if key != 'work_intervals'},
            'work_intervals': [[iso_utc(start), iso_utc(end)] for start, end in intervals]}
    cost = summary.get('window_demurrage_cents')
    if type(cost) is not int or cost < 0:
        raise ValueError('Invalid or unresolved window cost in validated checkpoint')
    return {
        'window_id': cp['window_id'], 'as_of_at': cp['as_of_at'],
        'has_work': bool(rows), 'schedule': schedule, 'schedule_hash': digest(schedule),
        'physical_schedule': physical_schedule if not missing_physical else None,
        'physical_schedule_hash': digest(physical_schedule) if not missing_physical else None,
        'missing_physical_evidence': missing_physical,
        'cost_cents': cost, 'first_domain_hash': first_hash,
        'final_domain_hash': final_hash,
        'initial_candidate_count': optimizer.get('initial_candidate_count'),
        'final_candidate_count': optimizer.get('final_candidate_count'),
        'final_domain_optimality_proven': final_domain.get('optimality_proven') is True,
        'solver_status': summary.get('solver_status'),
        'stopping_reason': optimizer.get('stopping_reason'),
        'rounds': [{'round': h.get('round'), 'candidate_count': h.get('candidate_count'),
                    'domain_hash': h.get('candidate_domain_fingerprint'),
                    'solver_status': h.get('solver_status'),
                    'validated_cost_cents': h.get('validated_cost_cents')}
                   for h in history],
    }


def read_run(report: dict, windows: list[dict], *, structural_valid: bool, replay_status: str | None = None, policy_fingerprint: str | None = None) -> dict:
    """Convert source-verified output to comparable metadata.

    The caller supplies the structural checker status and independent replay
    status; this function does not falsely assert that it performed them.
    """
    data = {
        'run_id': report.get('run_id'), 'status': report.get('status'),
        'source_fingerprint': report.get('source_fingerprint'),
        'policy_fingerprint': policy_fingerprint,
        'scope_call_ids': report.get('scope_call_ids'),
        'requested_windows': report.get('requested_windows'),
        'validated_window_count': report.get('validated_window_count'),
        'all_scope_calls_completed': report.get('all_scope_calls_completed'),
        'cumulative_demurrage_cents': report.get('cumulative_demurrage_cents'),
        'unresolved_accounting_call_ids': report.get('unresolved_accounting_call_ids'),
        'structural_valid': structural_valid,
        'independent_replay': replay_status,
        'windows': [read_window(cp) for cp in windows],
    }
    data['fully_validated_windows'] = (
        structural_valid and data['status'] == 'REQUESTED_WINDOWS_VALIDATED'
        and data['requested_windows'] == data['validated_window_count'] == len(data['windows'])
    )
    return data


def _range(values: list[int | float]):
    return [min(values), max(values)] if values else None


def compare_runs(runs: list[dict], *, expected_repetitions: int = 20) -> dict:
    """Compare window-specific ETBs, berths, costs and candidate domains.

    Never mistake a 2-window rolling projection for a finished 5-call schedule.
    Missing cells are reported rather than silently omitted from denominator.
    """
    if not runs:
        return {'version': VERSION, 'status': 'NO_RUNS', 'expected': expected_repetitions, 'observed': 0}
    baseline = runs[0]
    issues: list[dict] = []
    expected_scope = baseline.get('scope_call_ids')
    expected_source = baseline.get('source_fingerprint')
    expected_policy = baseline.get('policy_fingerprint')
    n_ok = 0
    valid = []
    for r in runs:
        if not r.get('fully_validated_windows'):
            issues.append({'run_id': r.get('run_id'), 'code': 'WINDOW_CHAIN_NOT_FULLY_VALIDATED'})
        if r.get('independent_replay') not in ('PASS',):
            issues.append({'run_id': r.get('run_id'), 'code': 'INDEPENDENT_REPLAY_NOT_PASSED', 'status': r.get('independent_replay')})
        if r.get('source_fingerprint') != expected_source or r.get('scope_call_ids') != expected_scope:
            issues.append({'run_id': r.get('run_id'), 'code': 'SOURCE_OR_SCOPE_CHANGED'})
        if r.get('policy_fingerprint') != expected_policy:
            issues.append({'run_id': r.get('run_id'), 'code': 'EFFECTIVE_POLICY_FINGERPRINT_CHANGED'})
        if not r.get('fully_validated_windows') or r.get('independent_replay') != 'PASS':
            continue
        n_ok += 1
        valid.append(r)
    by_window = defaultdict(list)
    for r in valid:
        for w in r.get('windows', []):
            by_window[w['window_id']].append((r['run_id'], w))
    comparisons = []
    for wid, selected in sorted(by_window.items()):
        reference = selected[0][1]
        hashes_first = sorted({w.get('first_domain_hash') for _, w in selected if w.get('first_domain_hash')})
        hashes_final = sorted({w.get('final_domain_hash') for _, w in selected if w.get('final_domain_hash')})
        missing_first = sum(1 for _, w in selected if w.get('has_work') and not w.get('first_domain_hash'))
        missing_final = sum(1 for _, w in selected if w.get('has_work') and not w.get('final_domain_hash'))
        missing_physical = sum(1 for _, w in selected if w.get('has_work') and not w.get('physical_schedule_hash'))
        physical_hashes = {w.get('physical_schedule_hash') for _, w in selected if w.get('has_work') and w.get('physical_schedule_hash')}
        schedules = sorted({w['schedule_hash'] for _, w in selected})
        costs = [w['cost_cents'] for _, w in selected]
        keyset = sorted({cid for _, w in selected for cid in w['schedule']})
        vessels = []
        for cid in keyset:
            present = [(rid, w['schedule'][cid]) for rid, w in selected if cid in w['schedule']]
            times = [int(datetime.fromisoformat(x['etb_utc']).timestamp()) for _, x in present]
            berths = sorted({x['berth_id'] for _, x in present})
            baserow = reference['schedule'].get(cid)
            minutes_from_baseline = []
            baseline_berth_switches = 0
            for _, value in present:
                if baserow is not None:
                    minutes_from_baseline.append(abs((datetime.fromisoformat(value['etb_utc']) - datetime.fromisoformat(baserow['etb_utc'])).total_seconds()) / 60)
                    baseline_berth_switches += int(value['berth_id'] != baserow['berth_id'])
            vessels.append({'call_id': cid, 'observed_runs': len(present), 'expected_valid_runs': len(selected),
                            'missing_runs': len(selected) - len(present),
                            'etb_span_minutes': (max(times)-min(times))/60 if times else None,
                            'max_abs_change_from_reference_minutes': max(minutes_from_baseline, default=None),
                            'mean_abs_change_from_reference_minutes': sum(minutes_from_baseline)/len(minutes_from_baseline) if minutes_from_baseline else None,
                            'berth_ids': berths, 'berth_variation': len(berths) > 1,
                            'reference_berth_changes': baseline_berth_switches})
        reference_differences = sum(
            sum(1 for cid, x in w['schedule'].items() if reference['schedule'].get(cid, {}).get('berth_id') != x['berth_id'])
            for _, w in selected
        )
        denom = sum(len(w['schedule']) for _, w in selected)
        all_optimal = all(w['final_domain_optimality_proven'] for _, w in selected)
        classifications = []
        if len(hashes_first) > 1: classifications.append('INITIAL_DOMAIN_DIFFERENCE')
        if len(hashes_final) > 1: classifications.append('EXPANDED_DOMAIN_DIFFERENCE')
        if len(set(costs)) > 1: classifications.append('COST_DIFFERENCE')
        if len(schedules) > 1: classifications.append('SCHEDULE_DIFFERENCE')
        if missing_first or missing_final:
            classifications.append('MISSING_DOMAIN_EVIDENCE')
        if missing_physical: classifications.append('MISSING_PHYSICAL_EVIDENCE')
        if len(physical_hashes) > 1: classifications.append('PHYSICAL_SCHEDULE_DIFFERENCE')
        if len(schedules) > 1 and not missing_final and len(hashes_final) == 1 and len(set(costs)) == 1:
            classifications.append('POSSIBLE_TIE_IF_ALL_SOLVES_PROVEN' if all_optimal else 'CANNOT_DISTINGUISH_TIE_FROM_EARLY_STOP')
        if not classifications: classifications.append('REPEATED_WINDOW_IDENTICAL')
        comparisons.append({'window_id': wid, 'comparisons': len(selected), 'initial_domain_unique_hashes': len(hashes_first),
                            'final_domain_unique_hashes': len(hashes_final), 'schedule_unique_hashes': len(schedules),
                            'missing_initial_domain_count': missing_first, 'missing_final_domain_count': missing_final,
                            'missing_physical_evidence_count': missing_physical, 'physical_schedule_unique_hashes': len(physical_hashes),
                            'schedule_hash_values': schedules, 'physical_schedule_hash_values': sorted(physical_hashes),
                            'cost_cents_min_max': _range(costs), 'all_final_domains_optimal_proven': all_optimal,
                            'solver_status_counts': dict(Counter(w.get('solver_status') for _, w in selected)),
                            'stopping_reason_counts': dict(Counter(w.get('stopping_reason') for _, w in selected)),
                            'berth_change_rate_vs_reference': reference_differences / denom if denom else None,
                            'max_etb_span_minutes': max((v['etb_span_minutes'] or 0 for v in vessels), default=0),
                            'vessels': vessels, 'diagnostic_flags': classifications})
    complete = len(runs) == expected_repetitions and n_ok == expected_repetitions and not issues
    all_same = bool(comparisons) and all(c['diagnostic_flags'] == ['REPEATED_WINDOW_IDENTICAL']
                                         and c['schedule_unique_hashes']==1
                                         and c['cost_cents_min_max'][0]==c['cost_cents_min_max'][1]
                                         for c in comparisons)
    missing_evidence = any('MISSING_DOMAIN_EVIDENCE' in c['diagnostic_flags'] or 'MISSING_PHYSICAL_EVIDENCE' in c['diagnostic_flags'] for c in comparisons)
    status = 'REPEATABLE_IN_TESTED_WINDOWS' if complete and all_same else 'INCOMPLETE_OR_UNREPLAYED' if not complete or missing_evidence else 'VARIATION_DETECTED'
    return {'version': VERSION, 'status': status, 'expected_repetitions': expected_repetitions,
            'run_count': len(runs), 'validated_and_replayed': n_ok, 'source_fingerprint': expected_source,
            'scope_call_ids': expected_scope, 'effective_policy_fingerprint': expected_policy,
            'all_scope_completed_runs': sum(bool(r.get('all_scope_calls_completed')) for r in runs),
            'cumulative_costs_resolved_runs': sum(type(r.get('cumulative_demurrage_cents')) is int for r in runs),
            'issues': issues, 'windows': comparisons,
            'interpretation': 'Finite candidate, synthetic, restricted-scope repeatability only. Not physical validation, 140-call completion or global optimality.'}


def compare_series(name: str, observations: list[dict], *, count: int = 20) -> dict:
    """A/B diagnostic status and hash/cost/schedule variation, no synthetic success."""
    valid = [o for o in observations if o.get('status') == 'PASS']
    hashes = sorted({o.get('domain_hash') for o in valid if o.get('domain_hash')})
    costs = [o['cost_cents'] for o in valid if type(o.get('cost_cents')) is int]
    schedules = sorted({o.get('schedule_hash') for o in valid if o.get('schedule_hash')})
    solver_statuses = dict(Counter(o.get('solver_status') for o in valid if o.get('solver_status')))
    missing_domains = sum(1 for o in valid if not o.get('domain_hash'))
    status = ('INCOMPLETE' if len(observations) != count or len(valid) != count
              else 'INCOMPLETE' if missing_domains
              else 'VARIATION_DETECTED' if len(hashes) > 1 or len(set(costs)) > 1 or len(schedules) > 1
                   or len(solver_statuses) > 1
              else 'REPEATED' if hashes else 'INCOMPLETE')
    return {'experiment': name, 'status': status, 'requested': count, 'observed': len(observations),
            'passed': len(valid), 'domain_hashes': len(hashes), 'missing_domain_count': missing_domains, 'cost_cents_min_max': _range(costs),
            'schedule_hashes': len(schedules), 'solver_status_counts': solver_statuses, 'errors': [o for o in observations if o.get('status') != 'PASS']}
