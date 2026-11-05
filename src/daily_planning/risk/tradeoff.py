"""Stage 1: deterministic, reproducible A/B/C trade-offs.

Input: normalized candidate dictionaries from candidate_reader (or fixtures).
No CP-SAT solve, DB mutation, or synthetic cost values.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from collections.abc import Mapping, Sequence


def _minute_diff(a: str, b: str) -> int:
    t1, t2 = datetime.fromisoformat(a), datetime.fromisoformat(b)
    if t1.tzinfo is None or t2.tzinfo is None:
        raise ValueError("Timezone-aware timestamps required")
    seconds = (t1 - t2).total_seconds()
    if seconds % 60:
        raise ValueError("Whole-minute times required")
    return int(seconds // 60)


def _row_map(candidate: Mapping) -> dict:
    rows = candidate["vessel_results"]
    indexed = {}
    for row in rows:
        cid = row["call_id"]
        if cid in indexed:
            raise ValueError(f"Duplicate call_id: {cid}")
        indexed[cid] = row
    return indexed


def _validate_candidate(candidate: Mapping) -> None:
    if type(candidate.get("cost_cents")) is not int or candidate["cost_cents"] < 0:
        raise ValueError("Expected integer nonnegative cost_cents")
    if type(candidate.get("wait_minutes")) is not int or candidate["wait_minutes"] < 0:
        raise ValueError("Expected integer nonnegative wait_minutes")
    rows = _row_map(candidate)
    if not rows:
        raise ValueError("Candidate with no evaluated vessels")
    costs = [r["demurrage_increment_to_completion_cents"] for r in rows.values()]
    if any(type(c) is not int or c < 0 for c in costs):
        raise ValueError("Invalid per-vessel demurrage cents")
    if sum(costs) != candidate["cost_cents"]:
        raise ValueError("Per-vessel demurrage sum does not match total")
    if sum(int(r["total_wait_minutes"]) for r in rows.values()) != candidate["wait_minutes"]:
        raise ValueError("Per-vessel wait sum does not match total")


def compare_candidates(candidates: Sequence[Mapping], *, baseline: str = "A") -> dict:
    """Compare same-run/same-snapshot candidates with explicit baseline label.

    Does not claim A is globally optimal; reports lowest *observed* cost.
    
    Candidate input fields:
      label, candidate_id, cost_cents, wait_minutes, solver_status,
      input_snapshot_hash, vessel_results[call_id,berth_id,work_start_at,
      total_wait_minutes,demurrage_increment_to_completion_cents].
    """
    if not candidates:
        raise ValueError("No candidates supplied")
    by = {}
    for c in candidates:
        if not c.get("label") or c["label"] in by:
            raise ValueError("Unique nonempty candidate labels required")
        _validate_candidate(c)
        by[c["label"]] = c
    if baseline not in by:
        raise ValueError("Baseline label is missing")
    hashes = {c.get("input_snapshot_hash") for c in by.values()}
    if None in hashes or len(hashes) != 1:
        raise ValueError("Candidate input snapshot hashes must match")
    call_sets = {tuple(sorted(_row_map(c))) for c in by.values()}
    if len(call_sets) != 1:
        raise ValueError("Candidates evaluate different vessel sets")

    ref = by[baseline]
    ref_rows = _row_map(ref)
    least = min(by.values(), key=lambda c: (c["cost_cents"],c["label"]))
    comparisons = []
    for label, cand in sorted(by.items()):
        rows = _row_map(cand)
        vessel_deltas = []
        changes, total_shift, largest_late, largest_early = 0,0,0,0
        for cid in sorted(rows):
            old, new = ref_rows[cid], rows[cid]
            cost_delta = new["demurrage_increment_to_completion_cents"] - old["demurrage_increment_to_completion_cents"]
            wait_delta = int(new["total_wait_minutes"]) - int(old["total_wait_minutes"])
            shift = _minute_diff(new["work_start_at"], old["work_start_at"])
            berth_changed = new["berth_id"] != old["berth_id"]
            changes += berth_changed
            total_shift += abs(shift)
            largest_late = max(largest_late, shift)
            largest_early = max(largest_early, -shift)
            vessel_deltas.append({
                "call_id": cid,
                "cost_delta_cents": cost_delta,
                "wait_delta_minutes": wait_delta,
                "berth_changed": berth_changed,
                "start_shift_minutes": shift,
                "reference_berth_id": old["berth_id"],
                "candidate_berth_id": new["berth_id"],
            })
        extra_cost = cand["cost_cents"] - ref["cost_cents"]
        wait_savings = ref["wait_minutes"] - cand["wait_minutes"]
        if sum(v["cost_delta_cents"] for v in vessel_deltas) != extra_cost:
            raise AssertionError("Vessel cost attribution mismatch")
        exchange = None
        if wait_savings > 0 and extra_cost > 0:
            value = (Decimal(extra_cost) * Decimal("0.6") / Decimal(wait_savings))
            exchange = format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),"f")
        if extra_cost < 0 and wait_savings > 0:
            category = "DOMINATES_BASELINE_ON_COST_AND_WAIT"
        elif extra_cost == 0 and wait_savings > 0:
            category = "FREE_WAIT_REDUCTION"
        elif wait_savings <= 0:
            category = "NO_WAIT_SAVINGS"
        else:
            category = "COST_FOR_WAIT_REDUCTION"
        comparisons.append({
            "label": label,
            "candidate_id": cand.get("candidate_id"),
            "solver_status": cand.get("solver_status", "UNKNOWN"),
            "total_cost_cents": cand["cost_cents"],
            "total_wait_minutes": cand["wait_minutes"],
            "extra_cost_vs_reference_cents": extra_cost,
            "wait_savings_vs_reference_minutes": wait_savings,
            "usd_per_wait_hour_saved": exchange,
            "tradeoff_category": category,
            "berth_change_count_vs_reference": changes,
            "total_absolute_start_shift_minutes": total_shift,
            "max_start_delay_minutes": largest_late,
            "max_start_advance_minutes": largest_early,
            "vessel_deltas": vessel_deltas,
        })
    return {
        "baseline_label": baseline,
        "input_snapshot_hash": next(iter(hashes)),
        "observed_lowest_cost_label": least["label"],
        "baseline_is_observed_cost_minimum": ref["cost_cents"] == least["cost_cents"],
        "optimality_scope": "FINITE_GENERATED_START_BERTH_DOMAIN_ONLY",
        "comparisons": comparisons,
    }
