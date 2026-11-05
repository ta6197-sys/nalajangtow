"""Read-only scenario replay using the established planning evaluator.

Only ``NO_SHOCK_BASELINE`` is implemented.  It deliberately delegates to
``evaluate_baseline`` so the existing 72-hour horizon, 24-hour freeze,
occupancy, workload, and contract-clock rules remain the single source of
truth.  Shock application is intentionally rejected until it can preserve
those same invariants.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from weather_optimizer import WeatherSeries

from ..evaluation import evaluate_baseline


NO_SHOCK_BASELINE = "NO_SHOCK_BASELINE"

class ScenarioReplayNotImplemented(NotImplementedError):
    pass


def _require_no_shock_baseline(scenario: Mapping[str, Any]) -> None:
    if scenario.get("scenario_id") != NO_SHOCK_BASELINE:
        raise ScenarioReplayNotImplemented(
            "Only NO_SHOCK_BASELINE is implemented; shock application is pending"
        )
    if scenario.get("additional_weather_block_minutes", 0) != 0:
        raise ValueError("NO_SHOCK_BASELINE cannot add a weather block")
    if scenario.get("weather_block_onset_offset_minutes") not in (None, 0):
        raise ValueError("NO_SHOCK_BASELINE cannot set a weather-block onset")
    for call_id, shock in scenario.get("call_shocks", {}).items():
        if not isinstance(shock, Mapping):
            raise ValueError(f"Invalid no-shock call entry: {call_id}")
        if shock.get("available_at_shift_minutes", 0) != 0:
            raise ValueError(f"NO_SHOCK_BASELINE cannot shift arrival: {call_id}")
        if shock.get("work_duration_multiplier", 1) != 1:
            raise ValueError(f"NO_SHOCK_BASELINE cannot change work duration: {call_id}")


def _planned_assignments(candidate: Mapping[str, Any]) -> list[dict]:
    rows = candidate.get("vessel_results")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Candidate must contain evaluated vessel_results")
    planned = []
    for row in rows:
        if row.get("assignment_kind") != "PLANNED":
            continue
        try:
            planned.append({
                "call_id": row["call_id"],
                "berth_id": row["berth_id"],
                "start_at": row["work_start_at"],
                "completion_at": row["completion_at"],
            })
        except KeyError as exc:
            raise ValueError("Planned candidate row is missing schedule data") from exc
    return planned


def _assert_candidate_matches_baseline(candidate: Mapping[str, Any], evaluation: Mapping[str, Any]) -> None:
    summary = evaluation["summary"]
    if candidate.get("cost_cents") != summary["demurrage_increment_to_completion_cents"]:
        raise AssertionError("NO_SHOCK_BASELINE cost differs from stored candidate evaluation")
    if candidate.get("wait_minutes") != summary["total_wait_minutes"]:
        raise AssertionError("NO_SHOCK_BASELINE wait differs from stored candidate evaluation")

    expected = {row["call_id"]: row for row in evaluation["vessel_results"]}
    actual_rows = candidate.get("vessel_results", [])
    actual = {row.get("call_id"): row for row in actual_rows}
    if None in actual or len(actual) != len(actual_rows) or set(actual) != set(expected):
        raise AssertionError("NO_SHOCK_BASELINE vessel set differs from stored candidate evaluation")
    for call_id, row in expected.items():
        stored = actual[call_id]
        for key in ("demurrage_increment_to_completion_cents", "total_wait_minutes", "completion_at"):
            if stored.get(key) != row[key]:
                raise AssertionError(
                    f"NO_SHOCK_BASELINE {key} differs for call {call_id}"
                )


def evaluate_scenario(
    snapshot: Mapping[str, Any],
    candidate: Mapping[str, Any],
    scenario: Mapping[str, Any],
    *,
    recovery_mode: str = "NO_RECOVERY",
) -> dict:
    """Replay a stored candidate for the implemented zero-shock scenario."""
    if recovery_mode not in ("NO_RECOVERY", "RECOVERY"):
        raise ValueError("Unsupported recovery mode")
    if recovery_mode != "NO_RECOVERY":
        raise ScenarioReplayNotImplemented("Recovery replay is not implemented")
    _require_no_shock_baseline(scenario)
    if candidate.get("input_snapshot_hash") != snapshot.get("snapshot_hash"):
        raise ValueError("Candidate input snapshot hash does not match replay snapshot")
    weather = WeatherSeries.from_csv(Path(snapshot["weather"]["path"]))
    replay = evaluate_baseline(snapshot, _planned_assignments(candidate), weather)
    evaluation = replay["evaluation"]
    _assert_candidate_matches_baseline(candidate, evaluation)
    summary = evaluation["summary"]
    return {
        "scenario_id": NO_SHOCK_BASELINE,
        "candidate_id": candidate.get("candidate_id"),
        "status": "SUCCEEDED",
        "cost_cents": summary["demurrage_increment_to_completion_cents"],
        "wait_minutes": summary["total_wait_minutes"],
        "completion_at": summary["last_completion_at"],
        "vessel_results": evaluation["vessel_results"],
        "constraint_violations": summary["constraint_violations"],
        "recovery_mode": recovery_mode,
    }
