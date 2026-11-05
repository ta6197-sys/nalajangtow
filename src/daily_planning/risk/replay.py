"""Read-only arrival-shock replay with fixed berth and vessel order."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

from weather_optimizer import WeatherSeries

from ..berth_phases import occupancy_bounds
from ..context import engine_calls, local_time, signature, verify_snapshot
from ..daylight import WORK_POLICY, daylight_berthing_start
from ..evaluation import evaluate_baseline, replay_row
from rolling_horizon import RULE
from weather_optimizer import at_datetime


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
        for key in ("demurrage_increment_to_completion_cents", "total_wait_minutes", "completion_at",
                    "berth_id", "work_start_at", "assignment_kind"):
            if stored.get(key) != row[key]:
                raise AssertionError(
                    f"NO_SHOCK_BASELINE {key} differs for call {call_id}"
                )


def _failure(scenario, candidate, reason):
    return dict(scenario_id=scenario.get('scenario_id'), candidate_id=candidate.get('candidate_id'),
                status='FAILED', failure_reason=str(reason), cost_cents=None,
                recovery_mode='NO_RECOVERY', constraint_violations=[str(reason)])


def _replay_arrivals(snapshot, candidate, scenario, weather):
    shocks = scenario.get('call_shocks', {})
    if not isinstance(shocks, Mapping):
        raise ValueError('call_shocks must be a mapping')
    original = {c['call_id']: c for c in snapshot['calls']}
    if set(shocks) - set(original):
        raise ValueError(f'Unknown shock calls: {sorted(set(shocks) - set(original))}')
    if scenario.get('additional_weather_block_minutes', 0) != 0:
        raise ValueError('Additional weather shock is unsupported')
    if scenario.get('weather_block_onset_offset_minutes') not in (None, 0):
        raise ValueError('Weather-block onset is unsupported')
    temporary = deepcopy(snapshot)
    for call in temporary['calls']:
        cid = call['call_id']
        shock = shocks.get(cid, {})
        if not isinstance(shock, Mapping):
            raise ValueError(f'Invalid call shock: {cid}')
        shift = shock.get('available_at_shift_minutes', 0)
        if type(shift) is not int:
            raise ValueError(f'Arrival shift must be integer minutes: {cid}')
        if shock.get('work_duration_multiplier', 1) != 1:
            raise ValueError(f'Work duration shock is unsupported: {cid}')
        call['available_at'] = (at_datetime(call['available_at']) + timedelta(minutes=shift)).isoformat()
    temporary['snapshot_hash'] = signature({k: v for k, v in temporary.items() if k != 'snapshot_hash'})
    calls = {c['call_id']: c for c in engine_calls(temporary)}
    begin = local_time(snapshot['as_of_at'])
    freeze = begin + timedelta(hours=24)
    policy = snapshot.get('berthing_policy')
    phase = snapshot.get('berth_phase')
    daylight = policy in ('DAYLIGHT_WEATHER_V1', WORK_POLICY)
    committed = {c['call_id'] for c in snapshot['commits']}
    by_berth = {}
    for row in candidate['vessel_results']:
        by_berth.setdefault(row['berth_id'], []).append(row)
    assignments, tail = [], []
    for berth, rows in by_berth.items():
        rows.sort(key=lambda r: (local_time(r.get('berthing_start_at', r['work_start_at'])), r['call_id']))
        released = None
        for old in rows:
            cid = old['call_id']
            start = local_time(old['work_start_at'])
            frozen = cid in committed or start < freeze
            proposed = max(start, local_time(calls[cid]['available_at']), released or start)
            if frozen:
                if proposed > start:
                    raise ValueError(f'FROZEN_ASSIGNMENT_CONFLICT: {cid} arrival or berth occupancy')
                new_start = start
            elif daylight:
                new_start = daylight_berthing_start(proposed, weather, RULE)
            else:
                new_start = weather.first_workable(proposed, RULE)
            new = replay_row(calls[cid], berth, new_start, weather,
                             committed=cid in committed, daylight=daylight,
                             policy=policy, phase_policy=phase)
            if frozen and (local_time(new['completion_at']) != local_time(old['completion_at'])
                           or local_time(occupancy_bounds(new)[1]) != local_time(old['berth_release_at'])):
                raise ValueError(f'FROZEN_ASSIGNMENT_CONFLICT: {cid} completion changed')
            released = local_time(occupancy_bounds(new)[1])
            if old['assignment_kind'] == 'PLANNED':
                assignments.append(dict(call_id=cid, berth_id=berth, start_at=new['work_start_at']))
            elif old['assignment_kind'] == 'EVALUATION_TAIL':
                tail.append(dict(call_id=cid, berth_id=berth, start_at=new['work_start_at']))
            elif old['assignment_kind'] != 'COMMITTED':
                raise ValueError(f'Unknown assignment kind: {cid}')
    result = evaluate_baseline(temporary, assignments, weather, fixed_tail_assignments=tail)
    return result['evaluation']


def evaluate_scenario(
    snapshot: Mapping[str, Any],
    candidate: Mapping[str, Any],
    scenario: Mapping[str, Any],
    *,
    recovery_mode: str = "NO_RECOVERY",
) -> dict:
    """Replay arrival shifts; return explicit failure for infeasible scenarios."""
    if recovery_mode not in ("NO_RECOVERY", "RECOVERY"):
        raise ValueError("Unsupported recovery mode")
    if recovery_mode != "NO_RECOVERY":
        raise ScenarioReplayNotImplemented("Recovery replay is not implemented")
    if scenario.get('scenario_id') == NO_SHOCK_BASELINE:
        _require_no_shock_baseline(scenario)
    elif not scenario.get('scenario_id'):
        raise ValueError('scenario_id required')
    if candidate.get("input_snapshot_hash") != snapshot.get("snapshot_hash"):
        raise ValueError("Candidate input snapshot hash does not match replay snapshot")
    verify_snapshot(snapshot)
    weather = WeatherSeries.from_csv(Path(snapshot["weather"]["path"]))
    replay = evaluate_baseline(snapshot, _planned_assignments(candidate), weather)
    baseline = replay["evaluation"]
    _assert_candidate_matches_baseline(candidate, baseline)
    if scenario.get('scenario_id') == NO_SHOCK_BASELINE:
        evaluation = baseline
    else:
        try:
            evaluation = _replay_arrivals(snapshot, candidate, scenario, weather)
        except (ValueError, KeyError) as exc:
            return _failure(scenario, candidate, exc)
    summary = evaluation["summary"]
    return {
        "scenario_id": scenario['scenario_id'],
        "candidate_id": candidate.get("candidate_id"),
        "status": "SUCCEEDED",
        "cost_cents": summary["demurrage_increment_to_completion_cents"],
        "wait_minutes": summary["total_wait_minutes"],
        "completion_at": summary["last_completion_at"],
        "vessel_results": evaluation["vessel_results"],
        "constraint_violations": summary["constraint_violations"],
        "recovery_mode": recovery_mode,
    }
