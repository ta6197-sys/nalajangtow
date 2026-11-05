"""Read-only shock replay with fixed decisions and separate physical realization."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

from weather_optimizer import WeatherSeries, price_contract
from rolling_horizon import RULE

from ..bootstrap_db import kst_time
from ..context import engine_calls, local_time, verify_snapshot
from ..daylight import WORK_POLICY
from ..evaluation import evaluate_baseline, price_until
from ..sk_physical_v3 import assert_eligible

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
    message = str(reason)
    affected = [message.split(':', 1)[1].strip()] if ':' in message else []
    return dict(scenario_id=scenario.get('scenario_id'), candidate_id=candidate.get('candidate_id'),
                status='FAILED', failure_reason=message, cost_cents=None,
                failure_class='INFEASIBLE', physical_success=False, settlement_success=False,
                physical_status='INFEASIBLE', commitment_status='ON_PLAN',
                settlement_status='UNRESOLVED', recovery_mode='NO_RECOVERY',
                reason_codes=[message.split(':')[0]], affected_call_ids=affected,
                causal_shocks=dict(scenario), constraint_violations=[message])


def _physical_replay(snapshot, candidate, shocks, weather):
    """Replay fixed decisions and exact remaining work, preserving pre-as-of intervals."""
    begin = kst_time(snapshot['as_of_at'])
    freeze = begin + timedelta(hours=24)
    daylight = snapshot.get('berthing_policy') in ('DAYLIGHT_WEATHER_V1', WORK_POLICY)
    committed = {c['call_id'] for c in snapshot['commits']}
    calls = {c['call_id']: c for c in snapshot['calls']}
    states = {s['call_id']: s for s in snapshot['call_states']}
    by_berth = {}
    for row in candidate['vessel_results']:
        by_berth.setdefault(row['berth_id'], []).append(row)
    rows = []
    for berth, ordered in by_berth.items():
        released = None
        predecessor = None
        for old in sorted(ordered, key=lambda r: (kst_time(r.get('berthing_start_at', r['work_start_at'])), r['call_id'])):
            cid = old['call_id']
            assert_eligible(snapshot, cid, berth)
            shock = shocks.get(cid, {})
            if not isinstance(shock, Mapping):
                raise ValueError(f'INVALID_CALL_SHOCK: {cid}')
            if any(type(shock.get(key, 0)) is not int for key in
                   ('available_at_shift_minutes', 'available_at_shift_seconds')):
                raise ValueError(f'INVALID_ARRIVAL_SHIFT: {cid}')
            shift = timedelta(minutes=shock.get('available_at_shift_minutes', 0),
                              seconds=shock.get('available_at_shift_seconds', 0))
            available = kst_time(calls[cid]['available_at']) + shift
            original_start = kst_time(old['work_start_at'])
            original_release = kst_time(old['berth_release_at'])
            phase_minutes = old.get('berthing_minutes', 0)
            unberthing_minutes = old.get('unberthing_minutes', 0)
            old_berthing = kst_time(old.get('berthing_start_at', old['work_start_at']))
            frozen = cid in committed or original_start < freeze
            started = original_start < begin
            if kst_time(old['completion_at']) <= begin:
                if released and released > old_berthing:
                    raise ValueError(f'BERTH_OVERLAP: {cid}')
                row = dict(old, planned_start_at=original_start.isoformat(),
                    planned_completion_at=kst_time(old['completion_at']).isoformat(),
                    planned_berth_release_at=original_release.isoformat(),
                    realized_start_at=original_start.isoformat(),
                    realized_completion_at=kst_time(old['completion_at']).isoformat(),
                    realized_berth_release_at=original_release.isoformat(),
                    berthing_start_at=old_berthing.isoformat(),
                    cargo_quantity_kg=calls[cid]['cargo_quantity_kg'],
                    remaining_work_kg_at_as_of=0, remaining_work_kg_at_24h=0,
                    remaining_work_kg_at_72h=0,
                    completed_work_kg_at_24h=calls[cid]['cargo_quantity_kg'],
                    completed_work_kg_at_72h=calls[cid]['cargo_quantity_kg'],
                    start_delay_seconds=0, completion_delay_seconds=0,
                    berth_release_delay_seconds=0, commitment_deviations=[],
                    start_delay_minutes=0, completion_delay_minutes=0,
                    berth_release_delay_minutes=0,
                    predecessor_call_id=predecessor, delay_causes=[], frozen=frozen)
                if any(berth_overlap(row, previous) for previous in rows):
                    raise ValueError(f'BERTH_OVERLAP: {cid}')
                rows.append(row)
                released, predecessor = original_release, cid
                continue
            if started:
                state = states[cid]
                if state['status'] != 'IN_PROGRESS' or state['berth_id'] != berth:
                    raise ValueError(f'HISTORICAL_STATE_MISMATCH: {cid}')
                if released and released > old_berthing:
                    raise ValueError(f'BERTH_OVERLAP: {cid}')
                historical = [[kst_time(a).isoformat(), min(kst_time(b), begin).isoformat()]
                              for a, b in old.get('work_intervals', []) if kst_time(a) < begin]
                if not historical:
                    cursor = original_start
                    for pause_start, pause_end in old.get('work_pause_intervals', []):
                        pause_start, pause_end = kst_time(pause_start), kst_time(pause_end)
                        if pause_start >= begin:
                            break
                        if cursor < pause_start:
                            historical.append([cursor.isoformat(), min(pause_start, begin).isoformat()])
                        cursor = max(cursor, pause_end)
                    if cursor < begin:
                        historical.append([cursor.isoformat(), begin.isoformat()])
                past_seconds = worked_seconds_until(historical, begin)
                remaining_minutes = state['contract_clock_state']['remaining_work_minutes']
                seconds = past_seconds + scaled_work_seconds(remaining_minutes,
                                     shock.get('work_duration_multiplier', 1))
                proposed = begin
                start = original_start
                continuation = first_workable_start(proposed.replace(tzinfo=None), weather,
                    daylight=snapshot.get('berthing_policy') == WORK_POLICY and cid not in committed)
                timeline = work_timeline(continuation,
                    seconds - past_seconds, weather,
                    daylight=snapshot.get('berthing_policy') == WORK_POLICY and cid not in committed)
                timeline['work_intervals'] = historical + timeline['work_intervals']
                timeline['work_start_at'] = start.isoformat()
                for key in ('weather_pause_intervals', 'night_pause_intervals'):
                    timeline[key] = [[kst_time(a).isoformat(), min(kst_time(b), begin).isoformat()]
                        for a, b in old.get(key, []) if kst_time(a) < begin] + timeline[key]
            else:
                if available < begin:
                    available = max(available, kst_time(calls[cid]['available_at']))
                proposed_berthing = max(old_berthing, available,
                                        released or old_berthing)
                proposed = proposed_berthing + timedelta(minutes=phase_minutes)
                start = first_workable_start(proposed.replace(tzinfo=None), weather,
                                             daylight=daylight and cid not in committed)
                seconds = scaled_work_seconds(calls[cid]['work_minutes'],
                                              shock.get('work_duration_multiplier', 1))
                timeline = work_timeline(start, seconds, weather,
                                         daylight=snapshot.get('berthing_policy') == WORK_POLICY
                                         and cid not in committed)
            completion = kst_time(timeline['completion_at'])
            release = completion + timedelta(minutes=unberthing_minutes)
            berth_start = old_berthing if started else kst_time(start) - timedelta(minutes=phase_minutes)
            row = dict(call_id=cid, berth_id=berth,
                       planned_available_at=kst_time(calls[cid]['available_at']).isoformat(),
                       realized_available_at=(kst_time(calls[cid]['available_at']) if started
                           else available).isoformat(),
                       work_start_at=kst_time(timeline['work_start_at']).isoformat(),
                       completion_at=completion.isoformat(), berth_release_at=release.isoformat(),
                       planned_start_at=original_start.isoformat(),
                       planned_completion_at=kst_time(old['completion_at']).isoformat(),
                       planned_berth_release_at=original_release.isoformat(),
                       realized_start_at=kst_time(timeline['work_start_at']).isoformat(),
                       realized_completion_at=completion.isoformat(),
                       realized_berth_release_at=release.isoformat(),
                       berthing_start_at=berth_start.isoformat(),
                       assignment_kind=old['assignment_kind'], frozen=frozen,
                       unberthing_minutes=unberthing_minutes,
                       cargo_quantity_kg=calls[cid]['cargo_quantity_kg'],
                       work_intervals=[[kst_time(a).isoformat(), kst_time(b).isoformat()]
                           for a,b in timeline['work_intervals']],
                       weather_pause_intervals=[[kst_time(a).isoformat(), kst_time(b).isoformat()]
                           for a,b in timeline['weather_pause_intervals']],
                       night_pause_intervals=[[kst_time(a).isoformat(), kst_time(b).isoformat()]
                           for a,b in timeline['night_pause_intervals']])
            if started:
                source_remaining = states[cid]['remaining_work_kg']
                future_seconds = seconds - past_seconds
                def balance(cut):
                    worked = worked_seconds_until(timeline['work_intervals'], cut) - past_seconds
                    return remaining_work_kg(source_remaining, future_seconds, worked)
            else:
                def balance(cut):
                    return remaining_work_kg(calls[cid]['cargo_quantity_kg'], seconds,
                        worked_seconds_until(timeline['work_intervals'], cut))
            row['remaining_work_kg_at_24h'] = balance(freeze)
            row['remaining_work_kg_at_72h'] = balance(begin + timedelta(hours=72))
            row['remaining_work_kg_at_as_of'] = (states[cid]['remaining_work_kg'] if started
                else calls[cid]['cargo_quantity_kg'])
            row['completed_work_kg_at_24h'] = (row['cargo_quantity_kg']
                                               - row['remaining_work_kg_at_24h'])
            row['completed_work_kg_at_72h'] = (row['cargo_quantity_kg']
                                               - row['remaining_work_kg_at_72h'])
            row['start_delay_seconds'] = int((kst_time(row['work_start_at']) - original_start).total_seconds())
            row['completion_delay_seconds'] = int((completion - kst_time(old['completion_at'])).total_seconds())
            row['berth_release_delay_seconds'] = int((release - original_release).total_seconds())
            for field in ('start', 'completion', 'berth_release'):
                row[f'{field}_delay_minutes'] = row[f'{field}_delay_seconds'] / 60
            row['commitment_deviations'] = [field for field in
                ('start', 'completion', 'berth_release') if frozen and
                row[f'{field}_delay_seconds'] != 0]
            row['predecessor_call_id'] = predecessor
            row['delay_causes'] = []
            if not started and shift.total_seconds() > 0 and available > original_start:
                row['delay_causes'].append('ARRIVAL_SHOCK')
            if shock.get('work_duration_multiplier', 1) != 1:
                row['delay_causes'].append('WORK_DURATION_SHOCK')
            if released and released > old_berthing:
                row['delay_causes'].append('PREVIOUS_BERTH_RELEASE')
            if any(kst_time(a) < kst_time(b) for a,b in row['weather_pause_intervals']):
                row['delay_causes'].append('WEATHER_INTERRUPTION')
            if any(berth_overlap(row, previous) for previous in rows):
                raise ValueError(f'BERTH_OVERLAP: {cid}')
            rows.append(row)
            released = release
            predecessor = cid
    return sorted(rows, key=lambda r: r['call_id'])


def _settle_replay(snapshot, candidate, physical, weather):
    """Price exact physical outcomes only at boundaries supported by contracts."""
    require_minute_aligned(physical)
    calls = {c['call_id']: c for c in engine_calls(snapshot)}
    originals = {r['call_id']: r for r in candidate['vessel_results']}
    begin = local_time(snapshot['as_of_at'])
    freeze = begin + timedelta(hours=24)
    horizon = begin + timedelta(hours=72)
    details = []
    for row in physical:
        cid = row['call_id']
        old = originals[cid]
        if kst_time(row['completion_at']) <= kst_time(snapshot['as_of_at']):
            details.append(dict(old, **row))
            continue
        call = calls[cid]
        start = local_time(row['work_start_at'])
        finish = local_time(row['completion_at'])
        work = [(local_time(a), local_time(b)) for a, b in row['work_intervals']]
        pauses = [[a.isoformat(), b.isoformat()] for (_, a), (b, _) in zip(work, work[1:]) if a < b]
        weather_pauses = [(local_time(a), local_time(b)) for a, b in row['weather_pause_intervals']]
        priced = price_contract(call['contract'], start, finish, weather_pauses, weather, RULE)
        pricing_row = dict(row, work_pause_intervals=pauses, contract_cost=priced)
        before = price_until(call, pricing_row, begin, weather)
        if before['demurrage_cents'] != old['accrued_before_cents']:
            raise ValueError(f'HISTORICAL_CONTRACT_MISMATCH: {cid}')
        at24 = price_until(call, pricing_row, min(freeze, finish), weather)
        at72 = price_until(call, pricing_row, min(horizon, finish), weather)
        increments = [x['demurrage_cents'] - before['demurrage_cents']
                      for x in (at24, at72, priced)]
        if any(x < 0 for x in increments) or increments != sorted(increments):
            raise ValueError(f'CONTRACT_COST_ORDER: {cid}')
        source = next(c for c in snapshot['calls'] if c['call_id'] == cid)
        available = local_time(row['realized_available_at'])
        detail = dict(old)
        detail.update(row)
        detail.update(work_pause_intervals=pauses,
            weather_pause_minutes=sum(int((b-a).total_seconds()/60) for a,b in weather_pauses),
            remaining_work_minutes_at_24h=None, remaining_work_minutes_at_72h=None,
            completed_at_72h=finish <= horizon,
            total_wait_minutes=max(0, int((start-available).total_seconds()/60)),
            remaining_wait_minutes=max(0, int((start-max(begin,available)).total_seconds()/60)),
            accrued_before_cents=before['demurrage_cents'],
            demurrage_increment_24h_cents=increments[0],
            demurrage_increment_72h_cents=increments[1],
            demurrage_increment_to_completion_cents=increments[2],
            total_settlement_demurrage_cents=priced['demurrage_cents'],
            despatch_to_completion_cents=priced['despatch_cents'],
            contract_clock_at_24h=at24, contract_clock_at_72h=at72,
            contract_clock_at_completion=priced)
        details.append(detail)
    return details


def _severity(physical):
    delayed = [r for r in physical if r['start_delay_seconds'] > 0]
    disrupted = [r for r in physical if r['commitment_deviations']]
    return dict(affected_call_ids=sorted({r['call_id'] for r in physical
        if any(r[f'{field}_delay_seconds'] for field in ('start','completion','berth_release'))}),
        commitment_affected_call_ids=[r['call_id'] for r in disrupted],
        propagated_delay_call_ids=[r['call_id'] for r in delayed
            if 'PREVIOUS_BERTH_RELEASE' in r['delay_causes']],
        total_start_delay_minutes=sum(r['start_delay_seconds'] for r in delayed)/60,
        max_completion_delay_minutes=max((r['completion_delay_seconds']/60 for r in physical), default=0),
        commitment_deviation_count=len(disrupted))


def evaluate_scenario(
    snapshot: Mapping[str, Any],
    candidate: Mapping[str, Any],
    scenario: Mapping[str, Any],
    *,
    recovery_mode: str = "NO_RECOVERY",
) -> dict:
    """Replay shocks without changing the snapshot or any planned decision."""
    if recovery_mode not in ("NO_RECOVERY", "LIMITED_RECOVERY"):
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
        details = baseline['vessel_results']
        physical = _physical_replay(snapshot, candidate, {},
            scenario_weather(weather, {}, local_time(snapshot['as_of_at'])))
    else:
        try:
            shocks = scenario.get('call_shocks', {})
            if not isinstance(shocks, Mapping):
                raise ValueError('INVALID_CALL_SHOCKS')
            known = {c['call_id'] for c in snapshot['calls']}
            if set(shocks) - known:
                raise ValueError(f'UNKNOWN_SHOCK_CALLS: {sorted(set(shocks)-known)}')
            weather = scenario_weather(weather, scenario, local_time(snapshot['as_of_at']))
            physical = _physical_replay(snapshot, candidate, shocks, weather)
            original_rows = {r['call_id']: r for r in baseline['vessel_results']}
            unchanged = all(all(kst_time(row[key]) == kst_time(original_rows[row['call_id']][key])
                for key in ('work_start_at', 'completion_at', 'berth_release_at')) and
                row['realized_available_at'] == row['planned_available_at'] and
                [[kst_time(a).isoformat(), kst_time(b).isoformat()]
                 for a,b in row['weather_pause_intervals']] ==
                original_rows[row['call_id']]['weather_pause_intervals']
                for row in physical)
            details = baseline['vessel_results'] if unchanged else _settle_replay(
                snapshot, candidate, physical, weather)
        except UnresolvedSubminuteSettlement as exc:
            severity = _severity(exc.physical_replay)
            return dict(_failure(scenario, candidate, exc),
                failure_class='UNRESOLVED', physical_success=True,
                physical_status='FEASIBLE',
                commitment_status='DISRUPTED' if severity['commitment_deviation_count'] else 'ON_PLAN',
                settlement_rule_status='UNRESOLVED_SUBMINUTE_SETTLEMENT_RULE',
                reason_codes=['UNRESOLVED_SUBMINUTE_SETTLEMENT_RULE'],
                physical_replay=exc.physical_replay, actual_schedules=exc.physical_replay,
                **severity)
        except (ValueError, KeyError, AssertionError) as exc:
            return _failure(scenario, candidate, exc)
    summary = baseline['summary']
    severity = _severity(physical) if physical else dict(
        affected_call_ids=[], commitment_affected_call_ids=[], propagated_delay_call_ids=[],
        total_start_delay_minutes=0, max_completion_delay_minutes=0,
        commitment_deviation_count=0)
    total = sum(r['demurrage_increment_to_completion_cents'] for r in details)
    return {
        "scenario_id": scenario['scenario_id'],
        "candidate_id": candidate.get("candidate_id"),
        "status": "SUCCEEDED",
        "physical_success": True,
        "settlement_success": True,
        "physical_status": "FEASIBLE", "commitment_status":
            'DISRUPTED' if severity['commitment_deviation_count'] else 'ON_PLAN',
        "settlement_status": "SUCCEEDED", "reason_codes": sorted(
            {code for row in physical for code in row['delay_causes']} |
            ({'COMMITMENT_DEVIATION'} if severity['commitment_deviation_count'] else set())),
        "cost_cents": total,
        "wait_minutes": sum(r['total_wait_minutes'] for r in details),
        "completion_at": max(r['completion_at'] for r in details),
        "vessel_results": details, "actual_schedules": physical if physical else details,
        "causal_shocks": dict(scenario),
        "constraint_violations": [],
        **severity,
        "recovery_mode": recovery_mode,
    }
