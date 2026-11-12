"""Additional read-only V5 S5 optimization evidence checks.

This is NOT a solver and is NOT an independent physical/inventory replay.
Run the repository's verify_resume.py after structural and arithmetic checks.
Do not sum 72-hour projected window costs as cumulative settlement.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

SCOPE_LABEL = "S5_5_CALLS_2_WINDOWS_ONLY"
EXPECTED_CALLS = 140
EXPECTED_BERTHS = 11
EXPECTED_TANKS = 3
EXPECTED_HARD_FIX = 15


def _instant(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("A timezone-aware timestamp is required")
    return result


def _cents(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def source_evidence(snapshot: dict, preflight: dict) -> dict:
    """Only verifies input evidence; cannot approve operational policies."""
    issues = []
    calls = snapshot.get("calls") or []
    berths = snapshot.get("berths") or []
    tanks = snapshot.get("tanks") or []
    fixed = [c for c in calls if c.get("fixed_etb_is_hard") is True]
    expected = (len(calls) == EXPECTED_CALLS and len(berths) == EXPECTED_BERTHS
                and len(tanks) == EXPECTED_TANKS and len(fixed) == EXPECTED_HARD_FIX)
    if not preflight.get("data_preflight_pass"):
        issues.append({"code": "SOURCE_PREFLIGHT_NOT_PASSED"})
    if not expected:
        issues.append({"code": "UNEXPECTED_SOURCE_CARDINALITY", "calls": len(calls),
                       "berths": len(berths), "tanks": len(tanks), "hard_fixed_etbs": len(fixed)})
    return {"status": "PASS_INPUT_STRUCTURE_ONLY" if not issues else "FAIL",
            "counts": {"calls": len(calls), "berths": len(berths),
                       "tanks": len(tanks), "hard_fixed_etbs": len(fixed)},
            "preflight_status": preflight.get("status"),
            "preflight_warnings": preflight.get("warnings", []),
            "simulation_only": True, "operational_approval": False, "issues": issues}


def inspect_s5_evidence(snapshot: dict, execution: dict, manifest: dict, windows: list[dict]) -> dict:
    """Audit selected schedule identities and exact integer-cent arithmetic.

    Call only AFTER the existing repository structural checker has passed.
    Does not trust checkpoint validation flags as an independent replay.
    """
    issues: list[dict] = []
    observations = []

    def issue(code: str, **details: Any) -> None:
        issues.append({"code": code, **details})

    config = manifest.get("config", {})
    calls = {c["call_id"]: c for c in snapshot.get("calls", []) if "call_id" in c}
    scope = config.get("scope", [])
    if len(scope) != 5 or len(set(scope)) != 5 or any(cid not in calls for cid in scope):
        issue("S5_SCOPE_NOT_EXACTLY_FIVE_SOURCE_CALLS")
    if config.get("windows") != 2 or len(windows) != 2:
        issue("S5_REQUIRES_TWO_COMPLETE_WINDOWS", saved_windows=len(windows))
    if config.get("rolling_optimizer_mode") != "INTEGRATED_COST_AWARE_V1":
        issue("WRONG_OPTIMIZER_MODE")
    if config.get("objective_mode") != "MIN_TOTAL_DEMURRAGE":
        issue("WRONG_OBJECTIVE_MODE")
    if config.get("inventory_constraint_mode") != "EXACT_EVENT_BALANCE_V1":
        issue("INVENTORY_NOT_IN_SOLVER")
    if execution.get("status") != "REQUESTED_WINDOWS_VALIDATED":
        issue("ROLLING_NOT_COMPLETED", status=execution.get("status"))
    if execution.get("validated_window_count") != len(windows):
        issue("REPORTED_WINDOW_COUNT_MISMATCH")
    if execution.get("requested_scope_count") != len(scope):
        issue("REPORTED_SCOPE_MISMATCH")
    if execution.get("operational_approval") is not False or execution.get("simulation_only") is not True:
        issue("MISSING_SYNTHETIC_DISCLAIMER")
    if execution.get("full_period_global_optimality_proven") is True:
        issue("UNSUPPORTED_GLOBAL_OPTIMALITY_CLAIM")
    if len(calls) != len(snapshot.get("calls", [])):
        issue("DUPLICATE_SOURCE_CALL_ID")

    selected_windows = 0
    union_seen: set[str] = set()
    for i, cp in enumerate(windows, 1):
        summary = cp.get("summary") or {}
        rows = cp.get("selected_schedule") or []
        candidate = cp.get("candidate")
        window = {"window_id": i, "selected_call_count": len(rows),
                  "projected_cost_cents_NOT_ADDITIVE": summary.get("window_demurrage_cents"),
                  "solver_status": summary.get("solver_status"),
                  "final_domain_optimality_proven": summary.get("final_domain_optimality_proven")}
        observations.append(window)
        if not isinstance(rows, list):
            issue("MALFORMED_SCHEDULE", window_id=i)
            continue
        ids = [r.get("call_id") for r in rows if isinstance(r, dict)]
        if len(ids) != len(rows) or len(set(ids)) != len(ids):
            issue("INVALID_OR_DUPLICATE_SCHEDULE_CALLS", window_id=i)
            continue
        if not set(ids).issubset(set(scope)):
            issue("SCHEDULE_OUTSIDE_REQUESTED_SCOPE", window_id=i)
        union_seen.update(ids)
        if rows:
            selected_windows += 1
            if not isinstance(candidate, dict) or candidate.get("status") != "VALIDATED":
                issue("MISSING_VALIDATED_CANDIDATE", window_id=i)
                continue
            if candidate.get("selected_schedule") != rows:
                issue("CANDIDATE_SCHEDULE_MISMATCH", window_id=i)
            breakdown = candidate.get("vessel_demurrage_breakdown")
            if not isinstance(breakdown, dict) or set(breakdown) != set(ids):
                issue("COST_BREAKDOWN_CALL_SET_MISMATCH", window_id=i)
            else:
                amounts = []
                for cid, item in breakdown.items():
                    if not isinstance(item, dict) or item.get("status") != "SETTLED" or not _cents(item.get("demurrage_cents")):
                        issue("INVALID_PER_CALL_SETTLEMENT", window_id=i, call_id=cid)
                    else:
                        amounts.append(item["demurrage_cents"])
                total = candidate.get("total_demurrage_cents")
                if (len(amounts) == len(ids) and
                        (not _cents(total) or sum(amounts) != total or
                         total != summary.get("window_demurrage_cents"))):
                    issue("INTEGER_CENT_COST_MISMATCH", window_id=i)
            optimizer = candidate.get("optimizer_result") or {}
            if optimizer.get("total_projected_demurrage_cents") != candidate.get("total_demurrage_cents"):
                issue("CP_SAT_SETTLEMENT_MISMATCH", window_id=i)
            proof = (optimizer.get("final_candidate_domain") or {}).get("optimality_proven")
            if summary.get("final_domain_optimality_proven") is True and proof is not True:
                issue("UNPROVEN_DOMAIN_REPORTED_OPTIMAL", window_id=i)
            if proof is not True and optimizer.get("optimization_status") == "OPTIMAL":
                issue("INCORRECT_OPTIMALITY_STATUS", window_id=i)
        else:
            if candidate is not None or summary.get("window_demurrage_cents") != 0:
                issue("MALFORMED_EMPTY_WINDOW", window_id=i)
        for row in rows:
            if not isinstance(row, dict) or row.get("call_id") not in calls:
                continue
            cid = row["call_id"]
            try:
                chosen = _instant(row["etb_at"])
                src = calls[cid]
                if src.get("fixed_etb_is_hard") is True:
                    if chosen != _instant(src["fix_etb_at"]):
                        issue("HARD_FIX_ETB_MOVED", window_id=i, call_id=cid)
                elif chosen < _instant(src["eta_at"]):
                    issue("ETB_BEFORE_ETA", window_id=i, call_id=cid)
                if not row.get("berth_id"):
                    issue("MISSING_BERTH_ASSIGNMENT", window_id=i, call_id=cid)
            except (KeyError, TypeError, ValueError) as exc:
                issue("INVALID_CALL_TIME", window_id=i, call_id=cid, error=str(exc))

    if not selected_windows:
        issue("NO_NONEMPTY_OPTIMIZATION_WINDOW")
    if not union_seen:
        issue("NO_SELECTED_CALL_EVIDENCE")

    return {"status": "PASS_EVIDENCE_ONLY" if not issues else "FAIL",
            "scope": SCOPE_LABEL, "selected_window_count": selected_windows,
            "unique_selected_call_count": len(union_seen),
            "requested_call_count": len(scope), "windows": observations,
            "cumulative_demurrage_cents": execution.get("cumulative_demurrage_cents"),
            "cumulative_demurrage_scope": execution.get("cumulative_demurrage_scope"),
            "unresolved_accounting_call_ids": execution.get("unresolved_accounting_call_ids"),
            "projected_window_costs_are_additive": False,
            "finite_candidate_domain_optimality_only": True,
            "independent_physical_inventory_settlement_replay_performed": False,
            "global_58_day_optimality_proven": False,
            "simulation_only": True, "operational_approval": False,
            "issues": issues}
