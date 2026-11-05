"""Read-only arrival-shock replay with fixed berth and vessel order."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

from weather_optimizer import WeatherSeries

from ..berth_phases import occupancy_bounds
from ..bootstrap_db import kst_time
from ..context import engine_calls, local_time, signature, verify_snapshot
from ..daylight import WORK_POLICY, daylight_berthing_start
from ..evaluation import evaluate_baseline, replay_row
from ..sk_physical_v3 import assert_eligible
from rolling_horizon import RULE

from .time_precision import (berth_overlap, first_workable_start, remaining_work_kg,
                             scaled_work_seconds, work_timeline, worked_seconds_until)
from .weather_overlay import scenario_weather
from .settlement import UnresolvedSubminuteSettlement, require_minute_aligned


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
    if scenario.get('weather_blocks'):
        raise ValueError('NO_SHOCK_BASELINE cannot add weather blocks')
    if scenario.get("weather_block_onset_offset_minutes") not in (None, 0):
        raise ValueError("NO_SHOCK_BASELINE cannot set a weather-block onset")
    if scenario.get('weather_block_onset_offset_seconds', 0):
        raise ValueError('NO_SHOCK_BASELINE cannot set a weather-block onset second')
    for call_id, shock in scenario.get("call_shocks", {}).items():
        if not isinstance(shock, Mapping):
            raise ValueError(f"Invalid no-shock call entry: {call_id}")
        if shock.get("available_at_shift_minutes", 0) != 0:
            raise ValueError(f"NO_SHOCK_BASELINE cannot shift arrival: {call_id}")
        if shock.get('available_at_shift_seconds', 0) != 0:
            raise ValueError(f'NO_SHOCK_BASELINE cannot shift arrival seconds: {call_id}')
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
        if actual[call_id] != row:
            differing = sorted(key for key in set(actual[call_id]) | set(row)
                               if actual[call_id].get(key) != row.get(key))
            raise AssertionError(
                f"NO_SHOCK_BASELINE vessel evaluation differs for {call_id}: {differing}"
            )


def _failure(scenario, candidate, reason):
    return dict(scenario_id=scenario.get('scenario_id'), candidate_id=candidate.get('candidate_id'),
                status='FAILED', failure_reason=str(reason), cost_cents=None,
                failure_class='INFEASIBLE', physical_success=False, settlement_success=False,
                recovery_mode='NO_RECOVERY', constraint_violations=[str(reason)])


def _physical_replay(snapshot, candidate, shocks, weather):
    """Retain the complete second-precision fixed order when pricing is undefined."""
    begin = local_time(snapshot['as_of_at'])
    freeze = begin + timedelta(hours=24)
    daylight = snapshot.get('berthing_policy') in ('DAYLIGHT_WEATHER_V1', WORK_POLICY)
    committed = {c['call_id'] for c in snapshot['commits']}
    calls = {c['call_id']: c for c in snapshot['calls']}
    by_berth = {}
    for row in candidate['vessel_results']:
        by_berth.setdefault(row['berth_id'], []).append(row)
    rows = []
    for berth, ordered in by_berth.items():
        released = None
        for old in sorted(ordered, key=lambda r: (kst_time(r['work_start_at']), r['call_id'])):
            cid = old['call_id']
            assert_eligible(snapshot, cid, berth)
            shock = shocks.get(cid, {})
            shift = timedelta(minutes=shock.get('available_at_shift_minutes', 0),
                              seconds=shock.get('available_at_shift_seconds', 0))
            available = kst_time(calls[cid]['available_at']) + shift
            original_start = kst_time(old['work_start_at'])
            proposed = max(original_start, available, released or original_start)
            frozen = cid in committed or original_start.replace(tzinfo=None) < freeze
            if frozen and proposed > original_start:
                raise ValueError(f'FROZEN_ASSIGNMENT_CONFLICT: {cid} arrival or berth occupancy')
            start = first_workable_start(proposed.replace(tzinfo=None), weather,
                                         daylight=daylight and cid not in committed)
            seconds = scaled_work_seconds(calls[cid]['work_minutes'],
                                          shock.get('work_duration_multiplier', 1))
            timeline = work_timeline(start, seconds, weather,
                                     daylight=snapshot.get('berthing_policy') == WORK_POLICY
                                     and cid not in committed)
            completion = kst_time(timeline['completion_at'])
            release = completion + timedelta(minutes=old.get('unberthing_minutes', 0))
            if frozen and (kst_time(old['completion_at']) != completion
                           or kst_time(old['berth_release_at']) != release):
                raise ValueError(f'FROZEN_ASSIGNMENT_CONFLICT: {cid} completion changed')
            row = dict(call_id=cid, berth_id=berth,
                       work_start_at=kst_time(timeline['work_start_at']).isoformat(),
                       completion_at=completion.isoformat(), berth_release_at=release.isoformat(),
                       cargo_quantity_kg=calls[cid]['cargo_quantity_kg'],
                       work_intervals=timeline['work_intervals'],
                       weather_pause_intervals=timeline['weather_pause_intervals'],
                       night_pause_intervals=timeline['night_pause_intervals'])
            row['remaining_work_kg_at_24h'] = remaining_work_kg(
                calls[cid]['cargo_quantity_kg'], seconds,
                worked_seconds_until(timeline['work_intervals'], freeze))
            row['remaining_work_kg_at_72h'] = remaining_work_kg(
                calls[cid]['cargo_quantity_kg'], seconds,
                worked_seconds_until(timeline['work_intervals'], begin + timedelta(hours=72)))
            row['completed_work_kg_at_24h'] = (row['cargo_quantity_kg']
                                               - row['remaining_work_kg_at_24h'])
            row['completed_work_kg_at_72h'] = (row['cargo_quantity_kg']
                                               - row['remaining_work_kg_at_72h'])
            if any(berth_overlap(row, previous) for previous in rows):
                raise ValueError(f'BERTH_OVERLAP: {cid}')
            rows.append(row)
            released = release
    return sorted(rows, key=lambda r: r['call_id'])


def _replay_arrivals(snapshot, candidate, scenario, weather):
    shocks = scenario.get('call_shocks', {})
    if not isinstance(shocks, Mapping):
        raise ValueError('call_shocks must be a mapping')
    original = {c['call_id']: c for c in snapshot['calls']}
    if set(shocks) - set(original):
        raise ValueError(f'Unknown shock calls: {sorted(set(shocks) - set(original))}')
    weather = scenario_weather(weather, scenario, local_time(snapshot['as_of_at']))
    temporary = deepcopy(snapshot)
    subminute_work = False
    for call in temporary['calls']:
        cid = call['call_id']
        shock = shocks.get(cid, {})
        if not isinstance(shock, Mapping):
            raise ValueError(f'Invalid call shock: {cid}')
        shift = shock.get('available_at_shift_minutes', 0)
        shift_seconds = shock.get('available_at_shift_seconds', 0)
        if type(shift) is not int or type(shift_seconds) is not int:
            raise ValueError(f'Arrival shift must be integer minutes/seconds: {cid}')
        seconds = scaled_work_seconds(call['work_minutes'], shock.get('work_duration_multiplier', 1))
        if seconds % 60:
            subminute_work = True
        else:
            call['work_minutes'] = seconds // 60
        call['available_at'] = (kst_time(call['available_at']) + timedelta(
            minutes=shift, seconds=shift_seconds)).isoformat()
    if (subminute_work or any(kst_time(c['available_at']).second for c in temporary['calls'])
            or any(a.second or b.second for a, b in weather.blocks)):
        physical = _physical_replay(snapshot, candidate, shocks, weather)
        require_minute_aligned(physical)
        raise UnresolvedSubminuteSettlement(physical)
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
        except UnresolvedSubminuteSettlement as exc:
            return dict(_failure(scenario, candidate, exc),
                        physical_replay=exc.physical_replay,
                        failure_class='UNRESOLVED', physical_success=True,
                        settlement_rule_status='UNRESOLVED_SUBMINUTE_SETTLEMENT_RULE')
        except (ValueError, KeyError) as exc:
            return _failure(scenario, candidate, exc)
    summary = evaluation["summary"]
    return {
        "scenario_id": scenario['scenario_id'],
        "candidate_id": candidate.get("candidate_id"),
        "status": "SUCCEEDED",
        "physical_success": True,
        "settlement_success": True,
        "cost_cents": summary["demurrage_increment_to_completion_cents"],
        "wait_minutes": summary["total_wait_minutes"],
        "completion_at": summary["last_completion_at"],
        "vessel_results": evaluation["vessel_results"],
        "constraint_violations": summary["constraint_violations"],
        "recovery_mode": recovery_mode,
    }
