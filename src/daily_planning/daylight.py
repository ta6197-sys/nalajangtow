"""Daylight berthing window for newly scheduled calls.

The source CSV contains official plan-date astronomy values. Weather remains
the existing historical scenario, and unloading may continue through night.
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
