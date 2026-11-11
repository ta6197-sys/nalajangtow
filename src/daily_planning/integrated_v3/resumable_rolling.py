"""Crash-resumable, read-only V3 72h rolling simulation.

Persist *only* independently validated 24h transitions. The next day is never
invented from a failed window, and saved results are replayed during resume.
"""
import json
import os
from datetime import timedelta
from pathlib import Path
from time import monotonic

from ..integrated_input.provenance import canonical, digest
from ..integrated_input.validator import timestamp
from .optimizer import CLEAR_BASELINE, optimize_72h
from .synthetic_rolling import simulated_boundary_state
from .validation_runner import validate_phase4_result

VERSION = "V3_RESUMABLE_COSTED_ROLLING_V1"
DEFAULT_STRATEGIES = (
    {"event_policy": "V3_ETB_EVENTS_V1", "berth_heuristic": "CRUDE_INBOUND_FIRST_V1"},
    {"event_policy": "V3_ETB_FLOW_EVENTS_V2", "berth_heuristic": "CRUDE_INBOUND_FIRST_V1"},
    {"event_policy": "V3_ETB_TENTATIVE_RELEASES_V3", "berth_heuristic": "CRUDE_INBOUND_FIRST_V1"},
)


def _fingerprint(obj):
    return digest(canonical(obj).encode("utf-8"))


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, payload, *, replace=False):
    """Atomic checkpoint with new-name protection. Never change source files."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise FileExistsError(str(path))
    temp = path.with_name("." + path.name + ".writing")
    if temp.exists():
        raise FileExistsError("A pending checkpoint exists: " + str(temp))
    try:
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() and not replace:
            raise FileExistsError(str(path))
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _rolling_state(snapshot, result, *, policy, completed_rows=()):
    candidate = result["incumbent"]
    tank = candidate["tank_validation"]
    projection = {
        "status": "PHYSICAL_AND_TANK_ONLY",
        "berth_result": {"schedule": candidate["berth_schedule"]},
        "validation": {
            "status": "PHYSICAL_AND_TANK_ONLY",
            "source_fingerprint": candidate["source_fingerprint"],
            "policy_fingerprint": candidate["policy_fingerprint"],
            "tank_result": tank,
        },
    }
    return simulated_boundary_state(snapshot, projection, policy=policy, completed_rows=completed_rows)


def _state_kwargs(state):
    if state is None:
        return {}
    return {key: state[key] for key in (
        "completed_rows", "commitments", "tank_commitments",
        "carried_transfers", "initial_inventory", "previous_schedule")}


def _validate_window(snapshot, result, *, policy, at, state, same_tank_policy):
    params = _state_kwargs(state)
    params.pop("previous_schedule", None)
    return validate_phase4_result(
        snapshot, result, policy=policy, as_of_at=at,
        same_tank_policy=same_tank_policy, settlement_records=(),
        pricing_policy=CLEAR_BASELINE, **params)


def _next_state(snapshot, result, *, policy, at, prior_state):
    candidate = result["incumbent"]
    if prior_state is not None:
        inventory = candidate["tank_validation"]["inventories"]
        for tid, expected in prior_state["initial_inventory"].items():
            if not inventory.get(tid) or inventory[tid][0]["inventory_bbl"] != expected:
                raise ValueError("CARRYOVER_MISMATCH: " + tid)
    state = _rolling_state(snapshot, result, policy=policy,
                           completed_rows=(prior_state or {}).get("completed_rows", ()))
    if timestamp(state["boundary"]) != timestamp(at) + timedelta(hours=24):
        raise ValueError("INVALID_24H_BOUNDARY")
    return state


def run_resumable_rolling(snapshot, *, policy, as_of_at, output_dir, days=58,
                          seconds_per_strategy=60, strategies=DEFAULT_STRATEGIES,
                          same_tank_policy=None, inventory_cuts=True,
                          inventory_relaxation=True, reuse_physical_model=True,
                          phase_release_policy="ALL_OPTIONS_V1", resume=False,
                          max_new_days=None, require_all_calls=True,
                          capacity_bound_policy='DISABLED'):
    """Run until completed, blocked or paused, committing each validated day.

    This is a synthetic clear-weather experiment and never approves operations.
    The same-tank rule must be supplied if the scenario requires it.
    """
    if type(days) is not int or not 1 <= days <= 366:
        raise ValueError("days must be 1..366")
    if isinstance(seconds_per_strategy, bool) or not isinstance(seconds_per_strategy, (int, float)) or seconds_per_strategy <= 0:
        raise ValueError("Positive per-strategy time budget required")
    if same_tank_policy not in (None, "ALLOW", "FORBID"):
        raise ValueError("Unknown same-tank policy")
    if phase_release_policy not in ("ALL_OPTIONS_V1", "LOCKED_ONLY_V1"):
        raise ValueError("Unknown phase release policy")
    if capacity_bound_policy not in ('DISABLED','V3_SINGLE_TANK_CAPACITY_BOUND_V1'):
        raise ValueError('Unknown tank capacity bound policy')
    if not isinstance(strategies, (list, tuple)) or not strategies:
        raise ValueError("Nonempty strategy list required")
    if max_new_days is not None and (type(max_new_days) is not int or max_new_days < 1):
        raise ValueError("max_new_days must be positive")
    for entry in strategies:
        if not isinstance(entry, dict) or set(entry) != {"event_policy", "berth_heuristic"}:
            raise ValueError("Each strategy needs event_policy and berth_heuristic")
        if entry["event_policy"] not in (
                "V3_ETB_EVENTS_V1", "V3_ETB_FLOW_EVENTS_V2", "V3_ETB_TENTATIVE_RELEASES_V3"):
            raise ValueError("Unknown event policy")
        if entry["berth_heuristic"] not in ("NONE", "CRUDE_INBOUND_FIRST_V1"):
            raise ValueError("Unknown berth heuristic")
    start = timestamp(as_of_at)
    folder = Path(output_dir)
    config = dict(version=VERSION, source_fingerprint=_fingerprint(snapshot),
                  policy_fingerprint=_fingerprint(policy), start_at=start.isoformat(),
                  days=days, seconds_per_strategy=seconds_per_strategy,
                  strategies=list(strategies), same_tank_policy=same_tank_policy,
                  inventory_cuts=inventory_cuts, inventory_relaxation=inventory_relaxation,
                  reuse_physical_model=reuse_physical_model,
                  phase_release_policy=phase_release_policy,
                  require_all_calls=require_all_calls,
                  pricing_policy=CLEAR_BASELINE)
    if capacity_bound_policy!='DISABLED':config['capacity_bound_policy']=capacity_bound_policy
    manifest = dict(run_fingerprint=_fingerprint(config), config=config,
                    operational_approval=False)
    manifest_path = folder / "manifest.json"
    if resume:
        if not manifest_path.exists() or _read(manifest_path) != manifest:
            raise ValueError("Resume fingerprint mismatch or manifest missing")
    else:
        if folder.exists() and any(folder.iterdir()):
            raise FileExistsError("Use a new output directory or --resume")
        _write(manifest_path, manifest)
    state = None
    completed = []
    covered = set()
    for day in range(1, days + 1):
        file = folder / ("day_%03d.json" % day)
        if not file.exists():
            if any((folder / ("day_%03d.json" % later)).exists()
                   for later in range(day + 1, days + 1)):
                raise ValueError("Noncontiguous saved checkpoints")
            break
        checkpoint = _read(file)
        at = (start + timedelta(days=day - 1)).isoformat()
        if (checkpoint.get("day") != day or checkpoint.get("as_of_at") != at or
                checkpoint.get("run_fingerprint") != manifest["run_fingerprint"] or
                checkpoint.get("prior_state_fingerprint") != _fingerprint(state)):
            raise ValueError("Checkpoint identity or previous state mismatch: " + str(file))
        audit = _validate_window(snapshot, checkpoint["result"], policy=policy, at=at,
                                 state=state, same_tank_policy=same_tank_policy)
        if audit["status"] != "PASS":
            raise ValueError("Saved checkpoint failed independent replay: " + str(file))
        next_state = _next_state(snapshot, checkpoint["result"], policy=policy, at=at,
                                 prior_state=state)
        if checkpoint.get("state_fingerprint") != _fingerprint(next_state):
            raise ValueError("Saved carry state mismatch: " + str(file))
        state = next_state
        completed.append(checkpoint["summary"])
        covered.update(x["call_id"] for x in checkpoint["result"]["incumbent"]["berth_schedule"])
    else:
        day = days + 1

    def summary(status, *, blocker=None):
        missing = sorted(set(x["call_id"] for x in snapshot["calls"]) - covered)
        return dict(model_version=VERSION, status=status, run_fingerprint=manifest["run_fingerprint"],
                    requested_days=days, validated_days=len(completed),
                    covered_call_count=len(covered), total_call_count=len(snapshot["calls"]),
                    uncovered_call_ids=missing, days=completed,
                    next_day=len(completed) + 1 if len(completed) < days else None,
                    blocker=blocker, last_simulated_state=state,
                    cost_scope="PER_WINDOW_GROSS_DEMURRAGE_NOT_SUMMED_DUE_TO_OVERLAP",
                    operational_approval=False)

    fresh_days = 0
    for day in range(len(completed) + 1, days + 1):
        if max_new_days is not None and fresh_days >= max_new_days:
            report = summary("PAUSED")
            _write(folder / "summary.json", report, replace=True)
            return report
        at = (start + timedelta(days=day - 1)).isoformat()
        attempts = []
        validated_result = None
        prior = _state_kwargs(state)
        for strategy in strategies:
            tick = monotonic()
            try:
                result = optimize_72h(
                    snapshot, as_of_at=at, policy=policy, settlement_records=[],
                    pricing_policy=CLEAR_BASELINE, time_limit_seconds=seconds_per_strategy,
                    same_tank_policy=same_tank_policy, inventory_cuts=inventory_cuts,
                    inventory_relaxation=inventory_relaxation,
                    reuse_physical_model=reuse_physical_model,
                    phase_release_policy=phase_release_policy,
                    candidate_event_policy=strategy["event_policy"],
                    berth_search_heuristic=strategy["berth_heuristic"],
                    **({'capacity_bound_policy':capacity_bound_policy} if capacity_bound_policy!='DISABLED' else {}), **prior)
                audit = _validate_window(snapshot, result, policy=policy, at=at,
                                         state=state, same_tank_policy=same_tank_policy)
                attempt = dict(strategy=strategy, solver_status=result.get("status"),
                               audit_status=audit["status"], reported_seconds=result.get("elapsed_seconds"),
                               measured_seconds=monotonic() - tick,
                               candidates_evaluated=len(result.get("iterations") or []),
                               issue_codes=[x.get("code") for x in audit.get("issues", [])[:10]],
                               best_cost_cents=(result.get("incumbent") or {}).get("total_demurrage_cents"))
                attempts.append(attempt)
                if audit["status"] == "PASS" and result.get("incumbent") is not None:
                    validated_result = result
                    break
            except (ValueError, KeyError, TypeError, OverflowError, RuntimeError, OSError) as exc:
                attempts.append(dict(strategy=strategy, solver_status="RUNNER_ERROR",
                                     audit_status="FAIL", measured_seconds=monotonic() - tick,
                                     issue_codes=["RUNNER_ERROR"], reason=str(exc)))
        if validated_result is None:
            blocker = dict(day=day, as_of_at=at, attempts=attempts,
                           code="NO_INDEPENDENTLY_VALIDATED_INCUMBENT")
            report = summary("STOPPED_UNRESOLVED", blocker=blocker)
            _write(folder / ("blocked_day_%03d.json" % day), blocker, replace=True)
            _write(folder / "summary.json", report, replace=True)
            return report
        try:
            next_state = _next_state(snapshot, validated_result, policy=policy, at=at,
                                     prior_state=state)
        except (ValueError, KeyError, TypeError) as exc:
            blocker = dict(day=day, as_of_at=at,
                           code="CARRYOVER_REPLAY_FAILED", reason=str(exc), attempts=attempts)
            report = summary("STOPPED_UNRESOLVED", blocker=blocker)
            _write(folder / ("blocked_day_%03d.json" % day), blocker, replace=True)
            _write(folder / "summary.json", report, replace=True)
            return report
        incumbent = validated_result["incumbent"]
        day_summary = dict(day=day, as_of_at=at, status="INDEPENDENTLY_VALIDATED_SYNTHETIC",
                           gross_demurrage_cents=incumbent["total_demurrage_cents"],
                           planned_call_count=len(incumbent["berth_schedule"]),
                           chosen_strategy=attempts[-1]["strategy"], attempts=attempts)
        checkpoint = dict(day=day, as_of_at=at,
                          run_fingerprint=manifest["run_fingerprint"],
                          prior_state_fingerprint=_fingerprint(state),
                          state_fingerprint=_fingerprint(next_state),
                          summary=day_summary, result=validated_result)
        _write(folder / ("day_%03d.json" % day), checkpoint)
        state = next_state
        completed.append(day_summary)
        covered.update(x["call_id"] for x in incumbent["berth_schedule"])
        fresh_days += 1
        _write(folder / "summary.json", summary("RUNNING"), replace=True)
    uncovered = set(x["call_id"] for x in snapshot["calls"]) - covered
    final_status = "REQUESTED_WINDOWS_VALIDATED" if not uncovered or not require_all_calls else "INCOMPLETE_CALL_COVERAGE"
    report = summary(final_status)
    _write(folder / "summary.json", report, replace=True)
    return report
