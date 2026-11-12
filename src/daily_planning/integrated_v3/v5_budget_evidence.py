"""Pure read-only comparison of independently verified V5 budget experiments.

No solver or physical/stock/contract validation occurs here. The caller must
supply source-verified, structural-validated, replay-PASS observations.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import mean

VERSION = "V5_STAGE2B_BUDGET_EVIDENCE_V1"


def utc(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timezone-aware ETB required")
    return result.astimezone(timezone.utc)


def window_evidence(valid_trials: list[dict]) -> list[dict]:
    """Compare windows within one budget, never summing overlapping window costs."""
    windows = defaultdict(list)
    for trial in valid_trials:
        for row in trial.get("windows", []):
            windows[row["window_id"]].append((trial["iteration"], row))
    result = []
    for window_id, entries in sorted(windows.items()):
        active = [r for _, r in entries if r.get('schedule')]
        first_hashes = {r.get('first_domain_hash') for r in active if r.get('first_domain_hash')}
        final_hashes = {r.get('final_domain_hash') for r in active if r.get('final_domain_hash')}
        missing_first = sum(not r.get('first_domain_hash') for r in active)
        missing_final = sum(not r.get('final_domain_hash') for r in active)
        missing_physical = sum(not r.get('physical_schedule_hash') for r in active)
        physical_hashes = {r['physical_schedule_hash'] for r in active if r.get('physical_schedule_hash')}
        schedules = {r.get("schedule_hash") for _, r in entries}
        costs = [r["cost_cents"] for _, r in entries]
        if not all(type(x) is int and x >= 0 for x in costs):
            raise ValueError("Unresolved/invalid projected window cents")
        seen = defaultdict(list)
        for iteration, row in entries:
            for call_id, assignment in row.get("schedule", {}).items():
                seen[call_id].append((iteration, assignment))
        vessels = []
        for call_id, assigned in sorted(seen.items()):
            times = [utc(x["etb_utc"]) for _, x in assigned]
            mins = (max(times) - min(times)).total_seconds() / 60
            berths = sorted({x["berth_id"] for _, x in assigned})
            vessels.append({"call_id": call_id, "observed": len(assigned),
                            "missing_runs": len(entries) - len(assigned),
                            "etb_span_minutes": mins, "berth_ids": berths,
                            "berth_varies": len(berths) > 1})
        candidate_counts = [r.get('final_candidate_count') for _, r in entries
                            if type(r.get('final_candidate_count')) is int]
        durations = [r.get('runtime_seconds') for _, r in entries
                     if isinstance(r.get('runtime_seconds'), (int, float))]
        canonical_counts = dict(Counter(str(r.get('canonical_status','UNREPORTED')) for _, r in entries))
        first = entries[0][1]['schedule']
        changed_vs_first = sum(
            sum(1 for cid in set(first) | set(row['schedule'])
                if first.get(cid) is None or row['schedule'].get(cid) is None
                or first[cid]['berth_id'] != row['schedule'][cid]['berth_id'])
            for _, row in entries
        )
        assignment_denominator = sum(len(row['schedule']) for _, row in entries)
        flags = []
        warnings = []
        if len(first_hashes) > 1:
            flags.append("INITIAL_DOMAIN_VARIATION")
        if len(final_hashes) > 1:
            flags.append("FINAL_DOMAIN_VARIATION")
        if len(set(costs)) > 1:
            flags.append("COST_VARIATION")
        if len(schedules) > 1:
            flags.append("SCHEDULE_VARIATION")
        if missing_first or missing_final:
            flags.append('MISSING_DOMAIN_EVIDENCE')
        if missing_physical:
            flags.append('MISSING_PHYSICAL_EVIDENCE')
        if len(physical_hashes) > 1:
            flags.append('PHYSICAL_SCHEDULE_VARIATION')
        if active and not all(r.get("final_domain_optimality_proven") is True for r in active):
            warnings.append("FINITE_DOMAIN_OPTIMALITY_NOT_PROVEN_IN_ALL_RUNS")
        if active and any(r.get('canonical_status') != 'PROVEN_CANONICAL_SELECTION' for r in active):
            warnings.append('CANONICAL_SELECTION_NOT_PROVEN_IN_ALL_RUNS')
        if any(v["missing_runs"] for v in vessels):
            flags.append("SCHEDULE_MEMBERSHIP_VARIATION")
        if len(schedules) > 1 and not missing_final and len(final_hashes) == len(set(costs)) == 1:
            flags.append("EQUAL_COST_SCHEDULE_VARIATION_CHECK_SECONDARY_STATUS")
        result.append({"window_id": window_id, "compared_runs": len(entries),
                       "initial_domain_hashes": len(first_hashes),
                       "final_domain_hashes": len(final_hashes),
                       "missing_initial_domain_count": missing_first,
                       "missing_final_domain_count": missing_final,
                       "missing_physical_evidence_count": missing_physical,
                       "physical_schedule_hashes": len(physical_hashes),
                       "schedule_hash_values": sorted(x for x in schedules if x),
                       "physical_schedule_hash_values": sorted(physical_hashes),
                       "schedule_hashes": len(schedules),
                       "cost_min_cents": min(costs), "cost_max_cents": max(costs),
                       "solver_status_counts": dict(Counter(str(r.get("solver_status")) for _, r in entries)),
                       "canonical_status_counts": canonical_counts,
                       "canonical_proven_runs": canonical_counts.get('PROVEN_CANONICAL_SELECTION', 0),
                       "final_candidate_count_min_max": ([min(candidate_counts),max(candidate_counts)] if candidate_counts else None),
                       "runtime_seconds_min_max": ([min(durations),max(durations)] if durations else None),
                       "runtime_seconds_mean": mean(durations) if durations else None,
                       "berth_or_membership_changes_vs_reference": changed_vs_first,
                       "berth_change_rate_vs_reference": (changed_vs_first/assignment_denominator if assignment_denominator else None),
                       "mean_etb_span_minutes": mean([v['etb_span_minutes'] for v in vessels]) if vessels else 0,
                       "stop_reason_counts": dict(Counter(str(r.get("stopping_reason")) for _, r in entries)),
                       "max_etb_span_minutes": max((v["etb_span_minutes"] for v in vessels), default=0),
                       "vessels": vessels, "diagnostic_flags": flags or ["OBSERVED_IDENTICAL"],
                       "warnings": warnings})
    return result


def budget_report(records: list[dict], *, expected: int, budget: int) -> dict:
    """Classify missing, failing, and repeated runs without hiding incomplete evidence."""
    unique = {r["iteration"] for r in records}
    if len(unique) != len(records):
        raise ValueError("Duplicate iteration records")
    valid = [r for r in records if r.get("status") == "PASS" and r.get("structural_valid") is True
             and r.get("replay_status") == "PASS" and r.get("execution_status") == "REQUESTED_WINDOWS_VALIDATED"]
    context_hashes = {
        (r.get('source_fingerprint'), r.get('policy_fingerprint'),
         tuple(r.get('scope_call_ids') or ()), r.get('as_of_at'))
        for r in valid
    }
    context_consistent = len(context_hashes) <= 1
    windows = window_evidence(valid)
    counts = Counter(r.get("status", "MISSING") for r in records)
    complete = len(records) == expected and len(valid) == expected
    consistent = bool(windows) and all(w["diagnostic_flags"] == ["OBSERVED_IDENTICAL"] for w in windows)
    if not complete:
        status = "INCOMPLETE_OR_FAILED"
    elif not context_consistent:
        status = "CONTEXT_MISMATCH"
    elif any('MISSING_DOMAIN_EVIDENCE' in w['diagnostic_flags'] or 'MISSING_PHYSICAL_EVIDENCE' in w['diagnostic_flags'] for w in windows):
        status = 'INCOMPLETE_EVIDENCE'
    elif consistent:
        status = "OBSERVED_REPEATABLE_AT_THIS_BUDGET"
    else:
        status = "VARIATION_DETECTED"
    return {"version": VERSION, "budget_seconds_per_window": budget,
            "expected": expected, "observed": len(records), "valid_and_replayed": len(valid),
            "status": status, "context_consistent": context_consistent,
            "status_counts": dict(counts), "windows": windows,
            "all_scope_completed_runs": sum(r.get("all_scope_calls_completed") is True for r in valid),
            "cumulative_cost_resolved_runs": sum(type(r.get("cumulative_demurrage_cents")) is int for r in valid),
            "failures": [{"iteration": r.get("iteration"), "status": r.get("status"),
                          "errors": r.get("errors", [])} for r in records if r not in valid],
            "note": "Projected 72h window costs overlap and MUST NOT be summed."}


def compare_budgets(records_by_budget: dict[int, list[dict]]) -> dict:
    """Only compare same-index, fully validated and replayed S5 iterations."""
    if len(records_by_budget) < 2:
        return {"status": "NEEDS_AT_LEAST_TWO_BUDGETS", "pairs": []}
    budgets = sorted(records_by_budget)
    reference = budgets[0]
    comparisons = []
    for budget in budgets[1:]:
        left = {r["iteration"]: r for r in records_by_budget[reference] if r.get("status") == "PASS"}
        right = {r["iteration"]: r for r in records_by_budget[budget] if r.get("status") == "PASS"}
        matched = sorted(set(left).intersection(right))
        by_window = defaultdict(list)
        for i in matched:
            a, b = left[i], right[i]
            if (a.get("source_fingerprint") != b.get("source_fingerprint")
                    or a.get("policy_fingerprint") != b.get("policy_fingerprint")
                    or a.get("scope_call_ids") != b.get("scope_call_ids")
                    or a.get("as_of_at") != b.get("as_of_at")):
                raise ValueError("Cross-budget source, policy, scope or date changed")
            for w_a, w_b in zip(a["windows"], b["windows"]):
                if w_a["window_id"] != w_b["window_id"]:
                    raise ValueError("Mismatched window identifiers")
                deltas = []
                changes = 0
                for cid in sorted(set(w_a["schedule"]) | set(w_b["schedule"])):
                    aa, bb = w_a["schedule"].get(cid), w_b["schedule"].get(cid)
                    if aa is None or bb is None:
                        changes += 1
                        continue
                    deltas.append(abs((utc(aa["etb_utc"]) - utc(bb["etb_utc"])).total_seconds()) / 60)
                    changes += int(aa["berth_id"] != bb["berth_id"])
                by_window[w_a["window_id"]].append({"iteration": i,
                    "cost_delta_cents": w_b["cost_cents"] - w_a["cost_cents"],
                    "same_initial_domain": bool(w_a.get("first_domain_hash") and w_b.get("first_domain_hash") and w_a["first_domain_hash"] == w_b["first_domain_hash"]),
                    "same_final_domain": bool(w_a.get("final_domain_hash") and w_b.get("final_domain_hash") and w_a["final_domain_hash"] == w_b["final_domain_hash"]),
                    "same_schedule": w_a["schedule_hash"] == w_b["schedule_hash"],
                    "same_physical_schedule": bool(w_a.get('physical_schedule_hash') and w_b.get('physical_schedule_hash') and w_a['physical_schedule_hash'] == w_b['physical_schedule_hash']),
                    "max_etb_delta_minutes": max(deltas, default=0), "berth_or_membership_changes": changes})
        windows = []
        for k, rows in sorted(by_window.items()):
            windows.append({"window_id": k, "matched_pairs": len(rows),
                            "cost_delta_cents_min_max": [min(r["cost_delta_cents"] for r in rows), max(r["cost_delta_cents"] for r in rows)],
                            "same_initial_domain_pairs": sum(r["same_initial_domain"] for r in rows),
                            "same_final_domain_pairs": sum(r["same_final_domain"] for r in rows),
                            "same_schedule_pairs": sum(r["same_schedule"] for r in rows),
                            "same_physical_schedule_pairs": sum(r["same_physical_schedule"] for r in rows),
                            "max_etb_delta_minutes": max(r["max_etb_delta_minutes"] for r in rows),
                            "total_berth_or_membership_changes": sum(r["berth_or_membership_changes"] for r in rows)})
        comparisons.append({"reference_budget_seconds": reference, "compared_budget_seconds": budget,
                            "matched_repetitions": len(matched), "windows": windows})
    return {"status": "COMPARISON_ONLY_NO_GLOBAL_OPTIMALITY_CLAIM", "pairs": comparisons,
            "caveat": "Different budgets may generate different finite domains; cost differences are diagnostic, not a proof of global optimum."}
