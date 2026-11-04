"""Pure BASELINE replay with a common FCFS evaluation tail."""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from rolling_horizon import RULE, partial_price, simulate_call, workload_remaining
from weather_optimizer import price_contract

from .bootstrap_db import kst_time, remaining_kg
from .context import engine_calls, local_time, plain, signature
from .daylight import WORK_POLICY, daylight_window, simulator_for_policy


def interval_overlap(a, b):
    return (a['berth_id'] == b['berth_id']
            and max(local_time(a['work_start_at']), local_time(b['work_start_at']))
            < min(local_time(a['completion_at']), local_time(b['completion_at'])))


def replay_row(call, berth_id, start_at, weather, *, committed=False, daylight=False, policy=None):
    start = local_time(start_at)
    if start < local_time(call['available_at']):
        raise ValueError(f'Start before availability: {call["call_id"]}')
    if daylight and not committed:
        sunrise, sunset = daylight_window(start)
        if not sunrise <= start < sunset:
            raise ValueError(f'Berthing outside sunrise/sunset: {call["call_id"]}')
    row = simulator_for_policy(policy)(call, start, weather, RULE)
    if local_time(row['work_start_at']) != start:
        raise ValueError(f'Planned start is weather-blocked: {call["call_id"]}')
    return dict(row, berth_id=berth_id)


def price_until(call, row, cut, weather):
    # Do not use future work to backdate the clock before that work started.
    started = row if row is not None and local_time(row['work_start_at']) < cut else None
    if started is None:
        notice_minutes = Decimal(str(call['contract']['notice_hours'])) * 60
        if notice_minutes != notice_minutes.to_integral_value():
            raise ValueError('Notice period must resolve to whole minutes')
        marker = local_time(call['contract']['nor_at']) + timedelta(minutes=int(notice_minutes))
        return price_contract(call['contract'], marker, cut, [], weather, RULE)
    return partial_price(call, started, cut, weather, RULE)


def evaluate_baseline(snapshot, planned_assignments, weather):
    """No DB writes, no reoptimization, no mutation of caller-owned objects."""
    if signature({k: v for k, v in snapshot.items() if k != 'snapshot_hash'}) != snapshot.get('snapshot_hash'):
        raise ValueError('Frozen input snapshot hash mismatch')
    begin = local_time(snapshot['as_of_at'])
    end = begin + timedelta(hours=72)
    freeze = begin + timedelta(hours=24)
    policy = snapshot.get('berthing_policy')
    daylight = policy in ('DAYLIGHT_WEATHER_V1', WORK_POLICY)
    calls = {c['call_id']: c for c in engine_calls(snapshot)}
    states = {s['call_id']: s for s in snapshot['call_states']}
    berths = {b['berth_id']: b for b in snapshot['berths']}
    active_berths = sorted(b for b, value in berths.items() if value['is_active'])
    if not active_berths:
        raise ValueError('No active berth for evaluation')
    rows, kinds = {}, {}
    for commit in snapshot['commits']:
        cid = commit['call_id']
        if cid not in calls or cid in rows or commit['berth_id'] not in berths:
            raise ValueError('Invalid or duplicate committed call')
        row = replay_row(calls[cid], commit['berth_id'], commit['start_at'], weather, committed=True, policy=policy)
        if (local_time(row['completion_at']) != local_time(commit['completion_at'])
                or local_time(commit['locked_from_at']) > local_time(row['work_start_at'])
                or local_time(commit['locked_until_at']) < local_time(row['completion_at'])):
            raise ValueError('Committed occupancy does not protect the full work interval')
        rows[cid], kinds[cid] = row, 'COMMITTED'
    for item in planned_assignments:
        cid = item['call_id']
        bid = item['berth_id']
        start = local_time(item.get('start_at', item.get('work_start_at')))
        if cid not in calls or cid in rows:
            raise ValueError(f'Unknown, duplicate or already committed call: {cid}')
        if bid not in active_berths or not begin <= start < end:
            raise ValueError('New decisions must use active berths and start inside the 72h horizon')
        row = replay_row(calls[cid], bid, start, weather, daylight=daylight, policy=policy)
        if item.get('completion_at') and local_time(item['completion_at']) != local_time(row['completion_at']):
            raise ValueError('Planned completion differs from weather/work replay')
        rows[cid], kinds[cid] = row, 'PLANNED'
    selected = list(rows.values())
    if any(interval_overlap(a, b) for i, a in enumerate(selected) for b in selected[i+1:]):
        raise ValueError('Candidate contains overlapping berth occupancy')
    for cid, state in states.items():
        if state['status'] == 'IN_PROGRESS' and cid not in rows:
            raise ValueError('In-progress work must have a protected assignment')
        if cid in rows and local_time(rows[cid]['work_start_at']) < begin:
            if state['status'] != 'IN_PROGRESS' or state['berth_id'] != rows[cid]['berth_id']:
                raise ValueError('Carried assignment differs from in-progress state')
            remaining = workload_remaining(rows[cid], calls[cid], begin)
            if (remaining != state['contract_clock_state'].get('remaining_work_minutes')
                    or remaining_kg(calls[cid]['cargo_quantity_kg'], remaining,
                                    calls[cid]['processing_minutes']) != state['remaining_work_kg']):
                raise ValueError('Carried workload differs from its original execution')
    deferred = sorted(set(calls) - set(rows))
    # All newly started tail work is >= horizon_end. Already chosen work can
    # extend past the boundary and continues occupying its berth.
    free = {b: max([end] + [local_time(r['completion_at']) for r in rows.values()
                           if r['berth_id'] == b]) for b in active_berths}
    for cid in sorted(deferred, key=lambda c: (local_time(calls[c]['available_at']), c)):
        bid = min(active_berths, key=lambda b: (free[b], b))
        simulate = simulator_for_policy(policy)
        row = simulate(calls[cid], max(free[bid], end, local_time(calls[cid]['available_at'])), weather, RULE)
        rows[cid] = dict(row, berth_id=bid)
        kinds[cid] = 'EVALUATION_TAIL'
        free[bid] = local_time(row['completion_at'])
    details = []
    occupancy = {b: 0 for b in berths}
    for cid, row in sorted(rows.items()):
        call = calls[cid]
        start, finish = local_time(row['work_start_at']), local_time(row['completion_at'])
        before = price_until(call, row, begin, weather)
        if states[cid]['accrued_demurrage_cents'] != before['demurrage_cents']:
            raise ValueError(f'Carried demurrage differs from the contract clock: {cid}')
        at24 = price_until(call, row, min(freeze, finish), weather)
        at72 = price_until(call, row, min(end, finish), weather)
        final = row['contract_cost']
        increments = [p['demurrage_cents']-before['demurrage_cents'] for p in (at24, at72, final)]
        if any(v < 0 for v in increments) or increments != sorted(increments):
            raise ValueError('Contract cost increments must be nonnegative and cumulative')
        remaining24 = workload_remaining(row, call, freeze)
        remaining72 = workload_remaining(row, call, end)
        pause72 = sum(max(0, int((min(local_time(b), end)-max(local_time(a), begin)).total_seconds()/60))
                      for a, b in row['weather_pause_intervals'])
        pauses_future = sum(max(0, int((local_time(b)-max(local_time(a), begin)).total_seconds()/60))
                            for a, b in row['weather_pause_intervals'])
        occupancy[row['berth_id']] += max(0, int((min(finish, end)-max(start, begin)).total_seconds()/60))
        details.append(dict(
            call_id=cid, assignment_kind=kinds[cid], berth_id=row['berth_id'],
            work_start_at=kst_time(start).isoformat(), completion_at=kst_time(finish).isoformat(),
            berth_release_at=kst_time(finish).isoformat(),
            weather_pause_intervals=[[kst_time(a).isoformat(), kst_time(b).isoformat()]
                                     for a, b in row['weather_pause_intervals']],
            work_intervals=[[kst_time(a).isoformat(), kst_time(b).isoformat()] for a,b in row.get('work_intervals', [])],
            work_pause_intervals=[[kst_time(a).isoformat(), kst_time(b).isoformat()] for a,b in row.get('work_pause_intervals', row['weather_pause_intervals'])],
            night_pause_intervals=[[kst_time(a).isoformat(), kst_time(b).isoformat()] for a,b in row.get('night_pause_intervals', [])],
            night_pause_minutes=sum(max(0, int((local_time(b)-max(local_time(a), begin)).total_seconds()/60)) for a,b in row.get('night_pause_intervals', [])),
            total_wait_minutes=int((start-local_time(call['available_at'])).total_seconds()/60),
            remaining_wait_minutes=max(0, int((start-max(begin, local_time(call['available_at']))).total_seconds()/60)),
            weather_pause_minutes=pauses_future, weather_pause_minutes_72h=pause72,
            remaining_work_minutes_at_24h=remaining24, remaining_work_minutes_at_72h=remaining72,
            remaining_work_kg_at_24h=remaining_kg(call['cargo_quantity_kg'], remaining24, call['processing_minutes']),
            remaining_work_kg_at_72h=remaining_kg(call['cargo_quantity_kg'], remaining72, call['processing_minutes']),
            completed_at_72h=finish <= end, accrued_before_cents=before['demurrage_cents'],
            demurrage_increment_24h_cents=increments[0], demurrage_increment_72h_cents=increments[1],
            demurrage_increment_to_completion_cents=increments[2],
            total_settlement_demurrage_cents=final['demurrage_cents'],
            despatch_to_completion_cents=final['despatch_cents'],
            contract_clock_at_24h=plain({k: str(Decimal(str(v))) if isinstance(v, float) else v
                                        for k, v in at24.items()}),
            contract_clock_at_72h=plain({k: str(Decimal(str(v))) if isinstance(v, float) else v
                                        for k, v in at72.items()}),
            contract_clock_at_completion=plain({k: str(Decimal(str(v))) if isinstance(v, float) else v
                                                for k, v in final.items()})))
    totals = ('demurrage_increment_24h_cents', 'demurrage_increment_72h_cents',
              'demurrage_increment_to_completion_cents', 'total_settlement_demurrage_cents',
              'despatch_to_completion_cents', 'total_wait_minutes', 'remaining_wait_minutes',
              'weather_pause_minutes', 'weather_pause_minutes_72h', 'night_pause_minutes', 'remaining_work_kg_at_72h')
    summary = {key: sum(r[key] for r in details) for key in totals}
    summary.update(unfinished_calls_at_72h=sum(not r['completed_at_72h'] for r in details),
                   last_completion_at=max((r['completion_at'] for r in details), default=None),
                   constraint_violations=[], call_count=len(details))
    previous={r['call_id']:r for r in snapshot.get('previous_tentative_assignments',[])}
    compared=[(r,previous[r['call_id']]) for r in details if r['call_id'] in previous]
    summary.update(berth_change_count=sum(r['berth_id']!=old['berth_id'] for r,old in compared),
                   start_shift_minutes=sum(abs(int((local_time(r['work_start_at'])-local_time(old['start_at'])).total_seconds()/60))
                                           for r,old in compared),
                   compared_tentative_call_count=len(compared))
    decisions = [dict(call_id=r['call_id'], berth_id=r['berth_id'], start_at=r['work_start_at'])
                 for r in details if r['assignment_kind'] != 'EVALUATION_TAIL']
    freeze_decisions = [r for r in decisions if local_time(r['start_at']) < freeze]
    return dict(schema_version=1, schedule_signature=signature(dict(assignments=decisions, deferred=deferred)),
                freeze_signature=signature(freeze_decisions), deferred_call_ids=deferred,
                evaluation=dict(evaluation_id=str(uuid.uuid4()), case='BASELINE', status='SUCCEEDED',
                                input_snapshot_hash=snapshot['snapshot_hash'],
                                tail_policy='FCFS_AVAILABLE_AT_CALL_ID_EARLIEST_FREE_BERTH',
                                summary=summary, vessel_results=details,
                                berth_metrics=[dict(berth_id=b, occupied_minutes_72h=minutes,
                                                    utilization_fraction_72h=minutes/(72*60))
                                               for b, minutes in sorted(occupancy.items())]))
