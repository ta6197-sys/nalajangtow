"""Read-only structured evidence for a failed V5 integrated Rolling window.

This does not change optimizer decisions, constraints, time budgets or source data.
It captures a bounded summary of the *actual* optimizer result while it is still
available in memory. A failed first window otherwise leaves no checkpoint.
"""
from __future__ import annotations

from collections import Counter
import os
import json
from pathlib import Path
from typing import Any

VERSION = "V5_FAILED_WINDOW_EVIDENCE_V1"
MAX_REJECTION_RECORDS = 5000


def _json_safe(value: Any) -> Any:
    """Return reproducible JSON primitives; do not silently stringify unknown objects."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), -float("inf")):
            raise ValueError("NaN/Infinity in evidence")
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    raise TypeError("Unsupported non-JSON evidence: " + type(value).__name__)


def _issues(rows: Any) -> list[dict]:
    if not isinstance(rows, list):
        return []
    return [_json_safe(row) for row in rows if isinstance(row, dict)]


def _domain_counts(domain: Any) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    if not isinstance(domain, dict):
        return {}, {}
    per_call: dict[str, dict[str, int]] = {}
    for cid, per_berth in sorted(domain.items()):
        if not isinstance(per_berth, dict):
            continue
        per_call[str(cid)] = {
            str(bid): len(times) if isinstance(times, list) else 0
            for bid, times in sorted(per_berth.items())
        }
    totals = {cid: sum(vals.values()) for cid, vals in per_call.items()}
    return per_call, totals


def _classify(*, status: Any, reason: Any, codes: set[str], missing: list[str],
              solver: Any) -> str:
    """Classification identifies where processing stopped, NOT physical root cause."""
    words = " ".join(str(v).upper() for v in [status, reason, *sorted(codes), solver])
    if "UNSUPPORTED_OPERATIONAL_POLICY" in words or "POLICY_UNRESOLVED" in words:
        return "OPERATIONAL_POLICY_UNRESOLVED"
    if "FIX_PRECHECK_FAILED" in words:
        return "HARD_FIX_OPTION_REJECTED"
    if "TIME_LIMIT" in words or "BUDGET_EXHAUSTED" in words:
        return "TIME_BUDGET_NO_VALIDATED_SOLUTION"
    if "SETTLEMENT" in words or "CLOCK_START_AUDIT_PENDING" in words:
        return "SETTLEMENT_OR_CLOCK_UNRESOLVED"
    if "NO_FEASIBLE_GENERATED_OPTION" in words or (missing and "INFEASIBLE" in words):
        return "SOME_CALLS_HAVE_NO_VALIDATED_PHYSICAL_OPTIONS"
    if "INVENTORY" in words:
        return "INVENTORY_CONSTRAINT_OR_REPLAY_FAILED"
    if "INFEASIBLE" in words or "NO_VALIDATED_CANDIDATE" in words:
        return "FINITE_CANDIDATE_DOMAIN_INFEASIBLE"
    if "VALIDATION_FAILED" in words or "REPLAY_FAILED" in words:
        return "INDEPENDENT_VALIDATION_FAILED"
    return "UNCLASSIFIED_REQUIRES_SOURCE_LOGS"


def build_failure_evidence(
    *, window_id: int, as_of_at: str, selected_call_ids: list[str],
    candidate: dict, source_fingerprint: str, policy_fingerprint: str,
    scenario_id: str | None = None,
) -> dict:
    """Summarize the live failed optimizer result; all inputs are read-only.

    Important: this is intentionally NOT a statement of infeasibility over
    continuous time or of full-period/global optimization.
    """
    if not isinstance(candidate, dict) or not isinstance(window_id, int) or window_id < 1:
        raise ValueError("Failed window evidence requires a candidate dict and valid window")
    if candidate.get("status") == "VALIDATED":
        raise ValueError("A validated candidate is not a failure")
    if not isinstance(selected_call_ids, list) or len(selected_call_ids) != len(set(selected_call_ids)):
        raise ValueError("Selected call IDs must be a unique list")
    optimizer = candidate.get("optimizer_result")
    optimizer = optimizer if isinstance(optimizer, dict) else {}
    physical = optimizer.get("physical_solver_result")
    physical = physical if isinstance(physical, dict) else {}
    domain, totals = _domain_counts(optimizer.get("candidates_by_call_and_berth"))
    missing = sorted(cid for cid in selected_call_ids if totals.get(cid, 0) == 0) if domain else []
    rejects = list(optimizer.get("rejection_reasons") or []) + list(physical.get("rejected_options") or [])
    rejects = [r for r in rejects if isinstance(r, dict)]
    counts = Counter(str(r.get("code", "UNSPECIFIED")) for r in rejects)
    rejected_by_call = Counter(str(r.get("call_id", "UNSPECIFIED")) for r in rejects)
    rejected_by_call_berth = {}
    for row in rejects:
        cid=str(row.get('call_id','UNSPECIFIED'));bid=str(row.get('berth_id','UNSPECIFIED'))
        counts_by_berth=rejected_by_call_berth.setdefault(cid,{})
        counts_by_berth[bid]=counts_by_berth.get(bid,0)+1
    issues = _issues(candidate.get("issues")) + _issues(optimizer.get("issues")) + _issues(physical.get("issues"))
    inventory_result = optimizer.get('inventory_result')
    inventory_result = inventory_result if isinstance(inventory_result, dict) else {}
    tank_result = inventory_result.get('tank_result')
    tank_result = tank_result if isinstance(tank_result, dict) else {}
    inventory_issues = (_issues(optimizer.get('inventory_violations')) +
                        _issues(inventory_result.get('issues')) + _issues(tank_result.get('issues')))
    codes = {str(i.get("code", "")) for i in issues}
    status = optimizer.get("status", candidate.get("status"))
    solver = optimizer.get("solver_status", physical.get("solver_status", physical.get("status")))
    report = {
        "evidence_version": VERSION,
        "window_id": window_id,
        "as_of_at": as_of_at,
        "scenario_id": scenario_id,
        "source_fingerprint": source_fingerprint,
        "policy_fingerprint": policy_fingerprint,
        "selected_call_ids": sorted(selected_call_ids),
        "selected_call_count": len(selected_call_ids),
        "validated": False,
        "status": candidate.get("status"),
        "reason": candidate.get("reason"),
        "optimizer_status": status,
        "solver_status": solver,
        "physical_solver_status": physical.get('solver_status', physical.get('status')),
        "settlement_validation": optimizer.get('settlement_validation'),
        "inventory_validation": optimizer.get('inventory_validation_status', optimizer.get('inventory_validation')),
        "stopping_reason": optimizer.get("stopping_reason"),
        "candidate_domain_fingerprint": optimizer.get("candidate_domain_fingerprint"),
        "initial_candidate_count": optimizer.get("initial_candidate_count"),
        "final_candidate_count": optimizer.get("final_candidate_count"),
        "domain_counts_by_call_and_berth": domain,
        "domain_counts_by_call": totals,
        "selected_calls_with_zero_candidates": missing,
        "generation_rounds": optimizer.get("generation_rounds") or [],
        "expansion_events": optimizer.get("expansion_events") or [],
        "issues": issues,
        "rejections_total": len(rejects),
        "rejection_counts_by_code": dict(sorted(counts.items())),
        "rejection_counts_by_call": dict(sorted(rejected_by_call.items())),
        "rejection_counts_by_call_and_berth": rejected_by_call_berth,
        "rejection_records": rejects[:MAX_REJECTION_RECORDS],
        "rejection_records_truncated": len(rejects) > MAX_REJECTION_RECORDS,
        "physical_solver_issues": _issues(physical.get("issues")),
        "inventory_issues": inventory_issues,
        "reoptimization_attempts": optimizer.get('reoptimization_attempts') or [],
        "physical_solver_option_count": physical.get("option_count"),
        "physical_solver_scope": physical.get("scope"),
        "operational_approval": False,
        "simulation_only": True,
        "infeasibility_claim_scope": "GENERATED_FINITE_CANDIDATE_DOMAIN_ONLY",
        "diagnostic_note": (
            "Failure classification is a triage label, not a proven root cause. "
            "Missing candidates may be caused by policy, FIX ETB, berth compatibility, "
            "work windows, or the candidate generator itself."
        ),
    }
    report["triage_classification"] = _classify(
        status=status, reason=candidate.get("reason"), codes=codes, missing=missing, solver=solver
    )
    return _json_safe(report)


def write_failure_evidence_new(path: str | Path, report: dict) -> None:
    """Never overwrite existing checkpoints or previous failure evidence."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_json_safe(report), ensure_ascii=False, sort_keys=True,
                         indent=2, allow_nan=False) + "\n"
    with p.open("x", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def failure_markdown(report: dict) -> str:
    def field(key: str) -> str:
        return str(report.get(key, "UNREPORTED"))
    per_call = report.get("domain_counts_by_call") or {}
    missing = report.get("selected_calls_with_zero_candidates") or []
    issue_lines = [f"- `{x.get('code','UNSPECIFIED')}` — {x.get('reason','')}" for x in report.get('issues', [])]
    rows = [f"| `{cid}` | {count} |" for cid, count in sorted(per_call.items())]
    return "\n".join([
        "# V5 실패 창 — 진단 증거", "",
        f"- Window: **{field('window_id')}** / `{field('as_of_at')}`",
        f"- Scenario: `{field('scenario_id')}` / `source={field('source_fingerprint')}`",
        f"- Status: `{field('status')}` / optimizer: `{field('optimizer_status')}`",
        f"- Solver: `{field('solver_status')}` / stopping: `{field('stopping_reason')}`",
        f"- Triage: **{field('triage_classification')}**", "",
        "## 후보 분석", "",
        f"- 초기/최종 후보: {field('initial_candidate_count')} / {field('final_candidate_count')}",
        f"- 0개 후보 항차: {', '.join(missing) if missing else 'NONE OBSERVED / DOMAIN MISSING'}",
        f"- 후보 지문: `{field('candidate_domain_fingerprint')}`", "",
        "| 항차 | 유효 후보 수 |", "|---|---:|", *rows, "",
        "## 실패 코드", "", *issue_lines, "",
        "## 해석 제한", "",
        "이 증거만으로 연속시간 전역 불가능이나 전체 항차의 물리적 불가능을 단정하지 않습니다.", "",
    ])
