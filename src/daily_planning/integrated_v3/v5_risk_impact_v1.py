"""V5 shock-impact assessment against attested Bridge V2 baseline.

This module does NOT optimize/re-time the schedule or simulate a new tank or
contract state. It identifies deterministic fixed-timetable conflicts for three
shock types. Unresolved consequences must never be presented as zero cost or
validated recovery. Exact integer seconds and rational arithmetic are used.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from hashlib import sha256
import json
from typing import Any, Mapping

VERSION = "V5_RISK_IMPACT_V1"
SCENARIO_VERSION = "V5_RISK_SCENARIO_V1"
MAX_SHOCKS = 50
MAX_DELAY_SECONDS = 30 * 86400
MAX_SHUTDOWN_SECONDS = 30 * 86400
MAX_MULTIPLIER = Fraction(4, 1)
TYPES = {"ETA_DELAY", "WORK_DURATION_MULTIPLIER", "WEATHER_CARGO_SHUTDOWN"}


class RiskImpactError(ValueError):
    """Invalid or ungrounded scenario; never infer missing inputs."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return sha256(canonical(value).encode("utf-8")).hexdigest()


def _time(value: str) -> datetime:
    if not isinstance(value, str):
        raise RiskImpactError("Timezone-aware string timestamp required")
    try:
        d = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RiskImpactError("Malformed ISO timestamp") from exc
    if d.utcoffset() is None:
        raise RiskImpactError("Timezone offset required")
    return d.astimezone(timezone.utc)


def _seconds_between(a: str, b: str) -> int:
    delta = _time(b) - _time(a)
    seconds = int(delta.total_seconds())
    if delta != timedelta(seconds=seconds):
        raise RiskImpactError("Subsecond precision not supported")
    return seconds


def _int(value: Any, label: str, *, lo: int = 1, hi: int = MAX_DELAY_SECONDS) -> int:
    if type(value) is not int or not lo <= value <= hi:
        raise RiskImpactError(label + " must be a bounded integer")
    return value


def _fraction(value: Any, label: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, str):
        raise RiskImpactError(label + " must be an exact numeric string")
    if len(value) > 48:
        raise RiskImpactError("Oversized exact numeric input")
    try:
        out = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise RiskImpactError("Invalid exact number: " + label) from exc
    if not Fraction(1) < out <= MAX_MULTIPLIER:
        raise RiskImpactError(label + " must exceed 1 and be <= 4")
    return out


def _ceil(x: Fraction) -> int:
    return -(-x.numerator // x.denominator)


def _only_keys(row: Mapping, required: set[str], optional: set[str] = frozenset()):
    if not isinstance(row, dict) or not required <= set(row) or set(row) - required - optional:
        raise RiskImpactError("Unsupported or missing scenario fields")


def validate_scenario(scenario: Mapping, *, scope: set[str], berth_ids: set[str]) -> dict:
    """Reject any source-mutating fields, duplicate IDs, or undefined entities."""
    _only_keys(scenario, {"version", "scenario_id", "shock_events"})
    if scenario["version"] != SCENARIO_VERSION:
        raise RiskImpactError("Unsupported risk scenario version")
    sid = scenario["scenario_id"]
    if not isinstance(sid, str) or not 1 <= len(sid) <= 64 or not all(c.isalnum() or c in "-_" for c in sid):
        raise RiskImpactError("Invalid scenario ID")
    events = scenario["shock_events"]
    if not isinstance(events, list) or len(events) > MAX_SHOCKS:
        raise RiskImpactError("Shock event count out of bounds")
    identifiers = set()
    normalized = []
    for e in events:
        if not isinstance(e, dict):
            raise RiskImpactError("Each event must be a mapping")
        kind = e.get("type")
        if kind not in TYPES:
            raise RiskImpactError("Unsupported shock type")
        eid = e.get("shock_id")
        if not isinstance(eid, str) or not 1 <= len(eid) <= 64 or not all(c.isalnum() or c in "-_" for c in eid) or eid in identifiers:
            raise RiskImpactError("Missing/duplicate/invalid shock ID")
        identifiers.add(eid)
        if kind == "ETA_DELAY":
            _only_keys(e, {"shock_id", "type", "call_id", "delay_seconds"})
            if e["call_id"] not in scope:
                raise RiskImpactError("ETA delay call is outside verified scope")
            _int(e["delay_seconds"], "delay_seconds")
        elif kind == "WORK_DURATION_MULTIPLIER":
            _only_keys(e, {"shock_id", "type", "call_id", "multiplier"})
            if e["call_id"] not in scope:
                raise RiskImpactError("Work-duration call is outside verified scope")
            _fraction(e["multiplier"], "multiplier")
        else:
            _only_keys(e, {"shock_id", "type", "start_at", "end_at", "berth_ids"})
            if not isinstance(e["berth_ids"], list) or not e["berth_ids"] or \
               any(not isinstance(b, str) or b not in berth_ids for b in e["berth_ids"]) or \
               len(e["berth_ids"]) != len(set(e["berth_ids"])):
                raise RiskImpactError("Explicit unique known berth IDs required")
            duration = _seconds_between(e["start_at"], e["end_at"])
            if not 0 < duration <= MAX_SHUTDOWN_SECONDS:
                raise RiskImpactError("Invalid weather cargo shutdown interval")
        normalized.append(deepcopy(e))
    return {"version": SCENARIO_VERSION, "scenario_id": sid, "shock_events": normalized}


def _overlap_seconds(a: str, b: str, lo: str, hi: str) -> int:
    start = max(_time(a), _time(lo))
    finish = min(_time(b), _time(hi))
    if finish <= start:
        return 0
    sec = int((finish - start).total_seconds())
    if start + timedelta(seconds=sec) != finish:
        raise RiskImpactError("Subsecond weather interval unsupported")
    return sec


def _work_seconds(row: Mapping) -> int:
    intervals = row.get("work_intervals")
    if not isinstance(intervals, list) or not intervals:
        raise RiskImpactError("Missing physical work intervals")
    total = 0
    previous_end = None
    for pair in intervals:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise RiskImpactError("Invalid physical interval")
        start, end = pair
        if previous_end is not None and _time(start) < _time(previous_end):
            raise RiskImpactError("Work intervals overlap or are unordered")
        sec = _seconds_between(start, end)
        if sec <= 0:
            raise RiskImpactError("Nonpositive work duration")
        total += sec
        previous_end = end
    if _time(intervals[0][0]) != _time(row["work_start_at"]) or \
       _time(intervals[-1][1]) != _time(row["work_end_at"]):
        raise RiskImpactError("Work interval endpoints differ from physical row")
    return total


def _get_windows(bundle: Mapping):
    """Access only V2 descriptors validated by structural attestation."""
    from .v5_simulation_bridge_v2 import verify_bundle_attestation, BRIDGE_VERSION
    if not isinstance(bundle, dict) or bundle.get("schema_version") != BRIDGE_VERSION:
        raise RiskImpactError("Source-bound Bridge V2 required")
    verify_bundle_attestation(bundle)
    if bundle.get("all_requested_windows_validated") is not True:
        raise RiskImpactError("Incomplete V5 Rolling baseline cannot be stress tested")
    return bundle["windows"]


def assess(bundle: Mapping, scenario: Mapping, *, baseline_audit: Mapping) -> dict:
    """Return evidence-backed risk impact screening; never claim a shock replay.

    The caller must obtain baseline_audit from verify_run_no_shock() against
    this same sourced run. Do not accept a status string as external authority.
    """
    windows = _get_windows(bundle)
    if not isinstance(baseline_audit, dict) or baseline_audit.get("status") != "NATIVE_NO_SHOCK_BASELINE_PARITY_PASS" or \
       baseline_audit.get("native_resume_replay") != "PASS" or \
       baseline_audit.get("validated_windows") != len(windows):
        raise RiskImpactError("Verified native NO_SHOCK replay required")
    if any(w.get("status") != "PHYSICAL_INVENTORY_SETTLEMENT_PARITY_PASS" for w in baseline_audit.get("window_parity", [])) or \
       len(baseline_audit.get("window_parity", [])) != len(windows):
        raise RiskImpactError("NO_SHOCK window parity evidence is incomplete")
    for i, (window, replay) in enumerate(zip(windows, baseline_audit["window_parity"]), 1):
        descriptor = window["baseline_descriptor_v1"]
        if replay.get("window_id") != i or replay.get("demurrage_cents") != \
           descriptor.get("window_projected_demurrage_cents") or \
           replay.get("schedule_row_count") != len(descriptor["selected_schedule"]):
            raise RiskImpactError("NO_SHOCK replay cannot be bound to the saved window")
    cfg = bundle["rolling_manifest"]["config"]
    scope = set(cfg["scope"])
    calls = {c["call_id"]: c for c in bundle["source_snapshot"]["calls"]}
    berth_ids = {b["berth_id"] for b in bundle["source_snapshot"]["berths"]}
    normalized = validate_scenario(scenario, scope=scope, berth_ids=berth_ids)
    events = normalized["shock_events"]
    per_event = []
    impacted_call_ids = set()
    impacted_window_ids = set()
    for e in events:
        kind = e["type"]
        hits = []
        for w in windows:
            d = w["baseline_descriptor_v1"]
            rows = d["selected_schedule"]
            admission = w.get("admission") or {}
            for row in rows:
                cid = row["call_id"]
                if kind in ("ETA_DELAY", "WORK_DURATION_MULTIPLIER") and cid != e["call_id"]:
                    continue
                if kind == "WEATHER_CARGO_SHUTDOWN" and row["berth_id"] not in e["berth_ids"]:
                    continue
                if kind == "ETA_DELAY":
                    delay = e["delay_seconds"]
                    eta = _time(calls[cid]["eta_at"])
                    pob = _time(row["pob_at"])
                    slack = int((pob - eta).total_seconds())
                    if eta + timedelta(seconds=delay) > pob:
                        verdict = "ETA_AFTER_POB_REPLAN_REQUIRED"
                        impacted_call_ids.add(cid)
                        impacted_window_ids.add(w["window_id"])
                    else:
                        verdict = "ETA_REMAINS_BEFORE_POB_FIXED_ROW_ONLY"
                    hits.append({"window_id": w["window_id"], "call_id": cid,
                        "berth_id": row["berth_id"], "baseline_pob_at": row["pob_at"],
                        "baseline_eta_at": calls[cid]["eta_at"],
                        "shocked_eta_at": (eta + timedelta(seconds=delay)).isoformat(),
                        "baseline_arrival_slack_seconds": slack,
                        "arrival_shortfall_seconds": max(0, delay-slack),
                        "hard_fix_etb": calls[cid]["fixed_etb_is_hard"],
                        "verdict": verdict})
                elif kind == "WORK_DURATION_MULTIPLIER":
                    seconds = _work_seconds(row)
                    multiplier = _fraction(e["multiplier"], "multiplier")
                    minimum = _ceil(Fraction(seconds)*multiplier)
                    delta = minimum-seconds
                    hits.append({"window_id":w["window_id"], "call_id":cid,
                         "berth_id":row["berth_id"], "baseline_work_seconds":seconds,
                         "minimum_required_work_seconds": minimum,
                         "extra_work_seconds_minimum": delta,
                         "baseline_work_end_at":row["work_end_at"],
                         "verdict":"DURATION_RATE_AND_WORK_WINDOW_REPLAY_REQUIRED"})
                    impacted_call_ids.add(cid)
                    impacted_window_ids.add(w["window_id"])
                else:
                    overlap = sum(_overlap_seconds(a,b,e["start_at"],e["end_at"])
                                   for a,b in row.get("work_intervals", []))
                    if overlap:
                        hits.append({"window_id":w["window_id"],"call_id":cid,
                              "berth_id":row["berth_id"],
                              "baseline_work_seconds_in_shutdown":overlap,
                              "verdict":"WEATHER_WORK_CONFLICT_REPLAY_REQUIRED"})
                        impacted_call_ids.add(cid)
                        impacted_window_ids.add(w["window_id"])
        due_windows = []
        if kind in ("ETA_DELAY", "WORK_DURATION_MULTIPLIER"):
            cid=e["call_id"]
            for w in windows:
                admitted=w.get("admission") or {}
                if cid in admitted.get("due_call_ids",[]):
                    due_windows.append(w["window_id"])
            # A due/deferred vessel can have no saved row. It still requires
            # admission-state replay after any nonzero arrival/work shock.
            if due_windows:
                impacted_call_ids.add(cid)
                impacted_window_ids.update(due_windows)
        weather_unassigned_due = set()
        if kind == "WEATHER_CARGO_SHUTDOWN":
            # A future, unscheduled candidate may also be affected. Without
            # rerunning candidate generation that effect cannot be ruled out.
            for w in windows:
                if _overlap_seconds(w["as_of_at"],w["baseline_descriptor_v1"].get("planning_horizon_end",
                                     w["as_of_at"]),
                                     e["start_at"],e["end_at"]):
                    impacted_window_ids.add(w["window_id"])
                    due_set = set(w["admission"].get("due_call_ids", []))
                    admitted_set = set(w["admission"].get("admitted_call_ids", []))
                    weather_unassigned_due.update(due_set - admitted_set)
        per_event.append({"shock_id": e["shock_id"], "type":kind,
            "evidence":hits,
            "due_window_ids_needing_admission_review":due_windows,
            "unassigned_due_call_ids_for_weather_eligibility_review": sorted(weather_unassigned_due),
            "impact_class":("POTENTIAL_FIXED_TIMETABLE_CONFLICT_OR_REEVALUATION"
                            if hits or due_windows or kind == "WEATHER_CARGO_SHUTDOWN"
                            else "OUTSIDE_SAVED_SCHEDULE_NO_COMPLETION_CLAIM"),
            "estimated_demurrage_delta_cents":None,
            "new_validated_schedule":"NOT_CALCULATED"})
    window_rollup = []
    for w in windows:
        wid = w["window_id"]
        local_hits = [e for e in per_event if any(h.get("window_id") == wid for h in e["evidence"])
                      or wid in e["due_window_ids_needing_admission_review"]]
        closure_horizon = [e for e in events if e["type"] == "WEATHER_CARGO_SHUTDOWN" and
                           _overlap_seconds(w["as_of_at"],
                             w["baseline_descriptor_v1"]["planning_horizon_end"],
                             e["start_at"], e["end_at"]) > 0]
        selected_ids = {r["call_id"] for r in w["baseline_descriptor_v1"]["selected_schedule"]}
        window_rollup.append({
            "window_id": wid,
            "baseline_selected_call_count": len(selected_ids),
            "baseline_deferred_call_count": len(w["admission"]["deferred_call_ids"]),
            "baseline_projected_cost_cents_NOT_ADDITIVE":
                w["baseline_descriptor_v1"].get("window_projected_demurrage_cents"),
            "event_ids_requiring_review": sorted({e["shock_id"] for e in local_hits} |
                                                 {e["shock_id"] for e in closure_horizon}),
            "shock_validation": "NOT_REPLAYED" if events else "NATIVE_NO_SHOCK_ONLY",
            "shocked_cost_cents": None,
        })
    latest = {v["call_id"]: v for v in windows[-1]["state_at_boundary"]}
    impacted_vessel_statuses = [deepcopy(latest[cid]) for cid in sorted(impacted_call_ids)]
    return {
        "schema_version":VERSION,
        "scenario_id":normalized["scenario_id"],
        "scenario_fingerprint":digest(normalized),
        "bridge_source_fingerprint":bundle["source_fingerprint"],
        "bridge_policy_fingerprint":bundle["policy_fingerprint"],
        "bridge_run_fingerprint":bundle["run_fingerprint"],
        "baseline_native_replay":"NATIVE_NO_SHOCK_BASELINE_PARITY_PASS",
        "baseline_window_count":len(windows),
        "baseline_window_projected_demurrage_cents_NOT_ADDITIVE":
            [{"window_id":w["window_id"], "cost_cents":w["baseline_descriptor_v1"].get("window_projected_demurrage_cents")}
             for w in windows],
        "shock_event_count":len(events),
        "shock_events":per_event,
        "window_impact_summary":window_rollup,
        "affected_vessels_at_last_verified_boundary":impacted_vessel_statuses,
        "affected_call_ids":sorted(impacted_call_ids),
        "affected_window_ids":sorted(impacted_window_ids),
        "status":("NO_SHOCK_NATIVE_PARITY_ONLY" if not events
                  else "SHOCK_IMPACT_SCREEN_ONLY_REPLAY_NOT_RUN"),
        "has_replanning_or_rolling_state_uncertainty":bool(events),
        "simulated_shocked_schedule":"NOT_RUN",
        "simulated_shocked_inventory":"NOT_RUN",
        "simulated_shocked_contract_settlement":"NOT_RUN",
        "scenario_demurrage_cents":None,
        "scenario_demurrage_delta_cents":None,
        "unpriced_deferred_liabilities":"NOT_ZERO_NOT_PRICED",
        "risk_engine_scope":"FIXED_TIMETABLE_IMPACT_SCREENING_NOT_INDEPENDENT_SHOCK_REPLAY",
        "state_origin":"SIMULATED_PROJECTION_NOT_OBSERVATION",
        "simulation_only":True,
        "operational_approval":False,
        "full_period_global_optimality_proven":False,
    }
