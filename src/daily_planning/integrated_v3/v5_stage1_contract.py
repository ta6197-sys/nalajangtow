"""V5 Stage 1: read-only run outcome and simulation baseline export contract.

No new berth choices, cost forecasts, physical claims, or contract prices are
calculated here. Input checkpoints MUST come from the validated V5 Rolling path.
This exporter verifies structural fingerprints, not independent physical replay.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Any, Mapping


def inspect_execution_coverage(snapshot, config, windows):
    """Check declared coverage and daylight inputs without inferring permissions."""
    import csv
    from pathlib import Path
    from zoneinfo import ZoneInfo
    from .v5_stage1_evidence import inspect_coverage
    result = inspect_coverage(snapshot, config, windows)
    blockers = result['blockers']
    for row in result['warnings']:
        if row.get('code') == 'DECLARED_BERTH_WINDOWS_NOT_CHECKABLE':
            blockers.append(dict(row, code='MISSING_DECLARED_BERTH_WINDOW'))
    context = config.get('pilotage_context') or {}
    try:
        if not context.get('daylight_source') or not isinstance(context.get('night_berthing_permissions'), dict):
            raise ValueError('Explicit daylight source and berth night permissions required')
        for berth in snapshot.get('berths', []):
            if berth.get('active') == 'Y' and type(context['night_berthing_permissions'].get(berth['berth_id'])) is not bool:
                raise ValueError('Missing night permission for ' + berth['berth_id'])
        with Path(context['daylight_path']).open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        start = _iso(config['as_of_at']).astimezone(ZoneInfo('Asia/Seoul')).date()
        end = _iso(required_horizon_end(config['as_of_at'], windows)).astimezone(ZoneInfo('Asia/Seoul')).date()
        days = {}
        for row in rows:
            date = row['date_kst']
            if date in days:
                raise ValueError('Duplicate daylight date: ' + date)
            rise = datetime.fromisoformat(date + 'T' + row['sunrise_kst'])
            sunset = datetime.fromisoformat(date + 'T' + row['sunset_kst'])
            if sunset <= rise:
                raise ValueError('Invalid daylight interval: ' + date)
            days[date] = row
        missing = [(start + timedelta(days=i)).isoformat() for i in range((end-start).days+1)
                   if (start + timedelta(days=i)).isoformat() not in days]
        if missing:
            raise ValueError('Missing daylight dates: ' + ', '.join(missing))
    except (KeyError, TypeError, ValueError, OSError) as exc:
        blockers.append({'code': 'MISSING_OR_INVALID_DAYLIGHT_PILOTAGE', 'detail': str(exc)})
    result['status'] = 'BLOCKED' if blockers else 'COVERAGE_PRECHECK_PASS'
    result['pilotage_note'] = ('Berth-wide windows do not override vessel-specific GRT, '
                               'daylight, night permission or departure restrictions.')
    return result

VERSION = "V5_SIMULATION_BRIDGE_DESCRIPTOR_V1"


class SimulationContractError(ValueError):
    """Reject missing or inconsistent evidence; never silently fill fields."""


def fingerprint(data: Any) -> str:
    return sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _iso(value: str) -> datetime:
    at = datetime.fromisoformat(value)
    if at.utcoffset() is None:
        raise SimulationContractError("Timezone-aware ISO timestamp required")
    return at


def required_horizon_end(as_of_at: str, windows: int) -> str:
    if type(windows) is not int or not 1 <= windows <= 65:
        raise SimulationContractError("Rolling windows must be 1..65")
    return (_iso(as_of_at) + timedelta(hours=(windows - 1) * 24 + 72)).isoformat()


def classify_coverage(coverage: Mapping[str, Any]) -> dict:
    """Conservative presentation of existing v5_stage1_evidence.inspect_coverage.

    This is NOT permission to extend process flows, invent daylight, or approve
    physical feasibility. Existing blocker details are returned verbatim.
    """
    blockers = list(coverage.get("blockers") or [])
    if coverage.get("status") == "COVERAGE_PRECHECK_PASS" and not blockers:
        return {"status": "COVERAGE_PRECHECK_PASS", "reason_category": None,
                "blockers": [], "warnings": list(coverage.get("warnings") or [])}
    codes = {b.get("code") for b in blockers if isinstance(b, Mapping)}
    if codes & {"FIXED_FLOW_HORIZON_SHORT", "FIXED_FLOW_INTERVALS_END_EARLY",
                "FIXED_FLOW_GAP_OR_INVALID_INTERVAL", "FIXED_FLOW_OVERLAP",
                "MISSING_FIXED_FLOW_COVERAGE_METADATA", "START_BEFORE_INITIAL_INVENTORY"}:
        reason = "FIXED_FLOW_COVERAGE_INCOMPLETE"
    elif codes & {"DECLARED_BERTH_WINDOW_HORIZON_SHORT", "MISSING_ACTIVE_BERTH_POLICY",
                  "INVALID_DECLARED_BERTH_WINDOW"}:
        reason = "DECLARED_BERTH_POLICY_COVERAGE_INCOMPLETE"
    else:
        reason = "INPUT_COVERAGE_OR_POLICY_UNRESOLVED"
    return {"status": "INPUT_COVERAGE_BLOCKED", "reason_category": reason,
            "blockers": deepcopy(blockers),
            "warnings": list(coverage.get("warnings") or [])}


def classify_run(report: Mapping[str, Any], *, coverage: Mapping[str, Any] | None = None) -> dict:
    """Separate window success, whole-scope completion and diagnostic failure.

    Never turn STOPPED_UNRESOLVED or EXECUTION_ERROR into successful completion.
    Do not infer a physical root cause from a single optimizer triage label.
    """
    raw = report.get("status")
    requested = report.get("requested_windows")
    validated = report.get("validated_window_count", 0)
    completed = report.get("completed_scope_count", 0)
    scope = report.get("requested_scope_count")
    if type(validated) is not int or validated < 0 or (requested is not None and
             (type(requested) is not int or not 1 <= requested <= 65 or validated > requested)):
        raise SimulationContractError("Invalid rolling window counts")
    if type(completed) is not int or completed < 0 or (scope is not None and
             (type(scope) is not int or completed > scope)):
        raise SimulationContractError("Invalid vessel completion counts")
    source_complete = report.get("all_scope_calls_completed")
    if type(source_complete) is not bool:
        raise SimulationContractError("Explicit all_scope_calls_completed flag required")
    if scope is not None and source_complete != (scope == completed):
        raise SimulationContractError("Completion flag/count mismatch")
    detail = {"raw_status": raw, "requested_windows": requested,
              "validated_windows": validated, "scope_calls": scope,
              "completed_calls": completed, "all_scope_calls_completed": source_complete,
              "stopping_reason": deepcopy(report.get("stopping_reason"))}
    if coverage is not None:
        c = classify_coverage(coverage)
        detail["input_coverage"] = c
        if c["status"] != "COVERAGE_PRECHECK_PASS":
            return dict(detail, execution_status="BLOCKED_INPUT_COVERAGE",
                        completion_status="NOT_VERIFIED", reason_category=c["reason_category"])
    if raw == "REQUESTED_WINDOWS_VALIDATED":
        if requested is None or validated != requested:
            raise SimulationContractError("False all-windows-validated claim")
        return dict(detail, execution_status="COMPLETED_REQUESTED_WINDOWS",
                    completion_status="ALL_CALLS_COMPLETE" if source_complete else "CALLS_REMAIN",
                    reason_category=None)
    if raw == "PAUSED":
        return dict(detail, execution_status="PAUSED", completion_status="NOT_VERIFIED",
                    reason_category="OPERATOR_OR_WINDOW_BUDGET_PAUSE")
    if raw == "STOPPED_UNRESOLVED":
        reason = report.get("stopping_reason") or {}
        code = reason.get("code") if isinstance(reason, Mapping) else None
        return dict(detail, execution_status="STOPPED_UNRESOLVED",
                    completion_status="NOT_VERIFIED", reason_category=code or "UNCLASSIFIED_STOP")
    if raw == "EXECUTION_ERROR":
        return dict(detail, execution_status="EXECUTION_ERROR",
                    completion_status="NOT_VERIFIED",
                    reason_category=report.get("error_code") or "UNKNOWN_EXCEPTION")
    return dict(detail, execution_status="UNKNOWN_STATUS", completion_status="NOT_VERIFIED",
                reason_category="UNSUPPORTED_RUN_STATUS")


def _validated_checkpoint(manifest: Mapping[str, Any], checkpoint: Mapping[str, Any]) -> None:
    config = manifest.get("config")
    if not isinstance(config, dict):
        raise SimulationContractError("Rolling manifest config required")
    if any(not isinstance(config.get(key), str) or not config[key].strip()
           for key in ('source_fingerprint', 'policy_fingerprint')):
        raise SimulationContractError("Source and policy fingerprints required")
    run_fp = manifest.get("run_fingerprint")
    if run_fp != fingerprint(config) or checkpoint.get("run_fingerprint") != run_fp:
        raise SimulationContractError("Rolling manifest fingerprint mismatch")
    if checkpoint.get("optimization_configuration") != config:
        raise SimulationContractError("Optimizer configuration mismatch")
    body = dict(checkpoint)
    saved_hash = body.pop("checkpoint_fingerprint", None)
    if saved_hash != fingerprint(body):
        raise SimulationContractError("Checkpoint fingerprint mismatch")
    after = checkpoint.get("next_state")
    if not isinstance(after, dict) or checkpoint.get("next_state_fingerprint") != fingerprint(after):
        raise SimulationContractError("Next-state fingerprint mismatch")
    validations = checkpoint.get("validation_results") or {}
    schedule = checkpoint.get("selected_schedule")
    if not isinstance(schedule, list):
        raise SimulationContractError("Selected schedule list required")
    if validations.get("physical") != ("VALID" if schedule else "NO_WORK") or any(
            validations.get(k) != value for k, value in
            (("inventory", "VALID"), ("settlement", "SETTLED" if schedule else "NO_WORK"),
             ("carryover", "VALID"))):
        raise SimulationContractError("Unvalidated V5 checkpoint")
    if checkpoint.get("input_fingerprint") != config.get("source_fingerprint") or \
            checkpoint.get("policy_fingerprint") != config.get("policy_fingerprint"):
        raise SimulationContractError("Source or policy fingerprint mismatch")
    if checkpoint.get("carried_inventory") != after.get("inventory") or \
            checkpoint.get("cargo_progress") != after.get("cargo_progress"):
        raise SimulationContractError("Carryover state mismatch")
    if config.get("rolling_optimizer_mode") != "INTEGRATED_COST_AWARE_V1" or \
            config.get("objective_mode") != "MIN_TOTAL_DEMURRAGE" or \
            config.get("inventory_constraint_mode") != "EXACT_EVENT_BALANCE_V1":
        raise SimulationContractError("Expected integrated V5 validated mode")
    if checkpoint.get("window_id", 0) < 1:
        raise SimulationContractError("Valid window ID required")
    if _iso(checkpoint["planning_horizon_end"]) != _iso(checkpoint["as_of_at"]) + timedelta(hours=72):
        raise SimulationContractError("Incorrect planning horizon")
    if _iso(after["at"]) != _iso(checkpoint["as_of_at"]) + timedelta(hours=24):
        raise SimulationContractError("Incorrect rolling state boundary")
    candidate = checkpoint.get("candidate")
    if schedule:
        if not isinstance(candidate, dict) or candidate.get("status") != "VALIDATED":
            raise SimulationContractError("Validated optimizer result is required")
        if candidate.get("selected_schedule") != schedule:
            raise SimulationContractError("Candidate/schedule mismatch")
        if candidate.get("total_demurrage_cents") != checkpoint.get("summary", {}).get("window_demurrage_cents"):
            raise SimulationContractError("Projected contract cost mismatch")
    elif candidate is not None:
        raise SimulationContractError("NO_WORK window must not claim a candidate")
    ids = [s.get("call_id") for s in schedule]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise SimulationContractError("Missing/duplicate scheduled call IDs")
    for row in schedule:
        for key in ("berth_id", "etb_at", "etu_at", "work_start_at", "work_end_at",
                    "work_intervals"):
            if key not in row:
                raise SimulationContractError("Missing physical timeline: " + key)


def build_window_descriptor(manifest: Mapping[str, Any], checkpoint: Mapping[str, Any],
                            *, prior_state: Mapping[str, Any] | None = None) -> dict:
    """Export a faithful V5 baseline descriptor for later zero-shock parity.

    This is not a full simulation input. Source snapshot, policy, pilotage,
    contracts and external flows must be attached/validated in Stage 2.
    Exact values and full physical/tank rows are preserved without approximation.
    """
    _validated_checkpoint(manifest, checkpoint)
    config = manifest["config"]
    raw_rows = checkpoint["selected_schedule"]
    candidate = checkpoint.get("candidate") or {}
    after = checkpoint["next_state"]
    if checkpoint['window_id'] > 1 and prior_state is None:
        raise SimulationContractError("Prior rolling state required after first window")
    if prior_state is not None:
        if fingerprint(prior_state) != checkpoint.get("prior_state_fingerprint"):
            raise SimulationContractError("Prior rolling state fingerprint mismatch")
        if _iso(prior_state["at"]) != _iso(checkpoint["as_of_at"]):
            raise SimulationContractError("Wrong prior rolling state boundary")
    scope = config.get("scope") or []
    if not isinstance(scope, list) or len(set(scope)) != len(scope):
        raise SimulationContractError("Invalid call scope")
    schedule_ids = {r["call_id"] for r in raw_rows}
    if not schedule_ids <= set(scope):
        raise SimulationContractError("Scheduled call outside manifest scope")
    prior_completed = {r["call_id"] for r in (prior_state or {}).get("completed_rows", [])}
    if prior_state is None:
        unscheduled = None  # Cannot infer pre-window state from T+24 checkpoint.
    else:
        unscheduled = sorted(set(scope) - prior_completed - schedule_ids)
    return {
        "schema_version": VERSION,
        "export_status": "DESCRIPTOR_ONLY_NOT_REPLAY_VALIDATED",
        "simulation_only": True,
        "operational_approval": False,
        "source_fingerprint": config["source_fingerprint"],
        "policy_fingerprint": config["policy_fingerprint"],
        "run_fingerprint": manifest["run_fingerprint"],
        "window_id": checkpoint["window_id"],
        "as_of_at": checkpoint["as_of_at"],
        "planning_horizon_end": checkpoint["planning_horizon_end"],
        "candidate_domain_fingerprint": (candidate.get("optimizer_result") or {}).get("candidate_domain_fingerprint"),
        "selected_schedule": deepcopy(raw_rows),
        "tank_result": deepcopy(candidate.get("tank_result")),
        "vessel_demurrage_breakdown": deepcopy(candidate.get("vessel_demurrage_breakdown")),
        "window_projected_demurrage_cents": checkpoint.get("summary", {}).get("window_demurrage_cents"),
        "prior_state": deepcopy(prior_state) if prior_state is not None else None,
        "next_state": deepcopy(after),
        "protected_commitments_in": deepcopy(checkpoint.get("protected_commitments_in") or []),
        "unassigned_call_ids_at_window_start": unscheduled,
        "unassigned_status": "UNKNOWN_NEEDS_PRIOR_STATE" if prior_state is None else "ID_LIST_ONLY_NEEDS_SOURCE_AND_REASON",
        "independent_simulation_replay_status": "NOT_RUN",
        "missing_required_replay_inputs": [
            *(["PRIOR_ROLLING_STATE"] if prior_state is None else []),
            "VERIFIED_V5_SOURCE_SNAPSHOT", "VERSIONED_OPERATING_POLICY",
            "VERSIONED_PILOTAGE_AND_DAYLIGHT", "EXTERNAL_PROCESS_FLOWS",
            "CONTRACT_SETTLEMENT_INPUTS", "V5_SHOCK_REPLAY_ENGINE",
        ],
        "notice": "One window only. Never sum overlapping rolling window projected costs.",
    }


def export_window_descriptor(run_directory, window_id, output_directory):
    """Read verified checkpoints and create a descriptor without overwriting evidence."""
    import os
    import tempfile
    from pathlib import Path
    from .v5_run_review import load_checkpoints
    if type(window_id) is not int or window_id < 1:
        raise SimulationContractError('Valid window ID required')
    source = Path(run_directory).resolve()
    target = Path(output_directory).resolve()
    if target == source or source in target.parents:
        raise SimulationContractError('Output must be separate from checkpoint directory')
    manifest, checkpoints = load_checkpoints(source)
    if window_id > len(checkpoints):
        raise SimulationContractError('Validated window not found')
    prior = checkpoints[window_id - 2]['next_state'] if window_id > 1 else None
    descriptor = build_window_descriptor(manifest, checkpoints[window_id - 1], prior_state=prior)
    target.mkdir(parents=True, exist_ok=True)
    destination = target / f'simulation_window_{window_id:03d}_descriptor_v1.json'
    fd, temp = tempfile.mkstemp(prefix='.descriptor-', dir=target)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(descriptor, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, destination)
    finally:
        os.unlink(temp)
    return destination
