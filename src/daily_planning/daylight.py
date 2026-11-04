"""Versioned daylight policies; V2 stops all physical work outside daylight.

V1 is retained only for replaying immutable historical experiments.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from functools import lru_cache

from rolling_horizon import simulate_call

from .context import ROOT, local_time


@lru_cache(maxsize=1)
def _windows():
    path = ROOT / 'src' / 'daily_planning' / 'daylight_windows.csv'
    windows = {}
    with path.open(encoding='utf-8-sig', newline='') as source:
        for row in csv.DictReader(source):
            day = row['date_kst']
            sunrise = datetime.fromisoformat(day + 'T' + row['sunrise_kst'])
            sunset = datetime.fromisoformat(day + 'T' + row['sunset_kst'])
            value = (sunrise, sunset)
            if day in windows and windows[day] != value:
                raise ValueError(f'Conflicting sunrise/sunset for {day}')
            windows[day] = value
    return windows


def daylight_window(at):
    day = local_time(at).date().isoformat()
    try:
        return _windows()[day]
    except KeyError as exc:
        raise ValueError(f'Missing sunrise/sunset for {day}') from exc


def daylight_berthing_start(proposed, weather, rule):
    """First minute at or after proposal that is both daylight and weather-safe."""
    at = local_time(proposed)
    while True:
        sunrise, sunset = daylight_window(at)
        if at < sunrise:
            at = sunrise
        if at >= sunset:
            at = datetime.combine(at.date() + timedelta(days=1), datetime.min.time())
            continue
        workable = weather.first_workable(at, rule)
        if workable < sunset:
            return workable
        at = datetime.combine(workable.date(), datetime.min.time())
        if workable.date() == sunrise.date():
            at += timedelta(days=1)


def simulate_new_call(call, proposed, weather, rule):
    start = daylight_berthing_start(proposed, weather, rule)
    row = simulate_call(call, start, weather, rule)
    if local_time(row['work_start_at']) != start:
        raise ValueError('Weather shifted a validated berthing start')
    return row


WORK_POLICY = 'DAYLIGHT_WORK_V2'


def _append_interval(intervals, start, end):
    if end <= start:
        return
    if intervals and intervals[-1][1] == start:
        intervals[-1] = (intervals[-1][0], end)
    else:
        intervals.append((start, end))


def simulate_daylight_call(call, proposed, weather, rule):
    """Consume work only in [sunrise, sunset), with separate weather/night stops.

    The berth stays occupied overnight. Night stops reduce physical progress;
    contract laytime still follows the supplied contract, not weather exclusions.
    """
    from weather_optimizer import price_contract
    required = int(call['processing_minutes'])
    if required <= 0:
        raise ValueError('Processing minutes must be positive')
    start = daylight_berthing_start(proposed, weather, rule)
    at, left = start, required
    work, weather_stops, night_stops, all_stops = [], [], [], []
    while left:
        sunrise, sunset = daylight_window(at)
        if not sunrise <= at < sunset:
            next_start = sunrise if at < sunrise else daylight_window(
                datetime.combine(at.date() + timedelta(days=1), datetime.min.time()))[0]
            _append_interval(night_stops, at, next_start)
            _append_interval(all_stops, at, next_start)
            at = next_start
            continue
        _, weather_end, _ = weather.slot(at, rule)
        boundary = min(weather_end, sunset)
        if weather.blocked(at, rule):
            _append_interval(weather_stops, at, boundary)
            _append_interval(all_stops, at, boundary)
            at = boundary
        else:
            consumed = min(left, int((boundary-at).total_seconds()/60))
            if consumed <= 0:
                raise ValueError('No forward progress in daylight work')
            end = at + timedelta(minutes=consumed)
            _append_interval(work, at, end)
            at, left = end, left-consumed
    price = price_contract(call['contract'], start, at, weather_stops, weather, rule)
    encoded = lambda intervals: [[a.isoformat(), b.isoformat()] for a, b in intervals]
    return dict(call_id=call['call_id'], nor_at=call['available_at'],
        work_start_at=start.isoformat(), completion_at=at.isoformat(),
        work_intervals=encoded(work), work_pause_intervals=encoded(all_stops),
        weather_pause_intervals=encoded(weather_stops), night_pause_intervals=encoded(night_stops),
        weather_pause_minutes=sum(int((b-a).total_seconds()/60) for a,b in weather_stops),
        night_pause_minutes=sum(int((b-a).total_seconds()/60) for a,b in night_stops),
        clock_start_at=price['clock_start_at'], demurrage_minutes=price['demurrage_minutes'],
        demurrage_cents=price['demurrage_cents'], despatch_cents=price['despatch_cents'],
        net_owner_receivable_cents=price['net_owner_receivable_cents'], contract_cost=price)


def simulator_for_policy(policy):
    if policy == WORK_POLICY:
        return simulate_daylight_call
    if policy == 'DAYLIGHT_WEATHER_V1':
        return simulate_new_call
    if policy in (None, 'LEGACY_UNRESTRICTED'):
        return simulate_call
    raise ValueError(f'Unsupported operating policy: {policy}')
