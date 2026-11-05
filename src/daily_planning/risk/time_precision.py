"""Second-precision physical work replay; the weather source remains a 30-minute grid."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from rolling_horizon import RULE

from ..bootstrap_db import kst_time
from ..daylight import daylight_window


def _risk_daylight_window(at: datetime):
    # The legacy lookup requires minute timestamps; only its date is relevant.
    # Physical Risk timestamps retain their seconds outside this lookup.
    return daylight_window(at.replace(second=0, microsecond=0))


def scaled_work_seconds(work_minutes: int, multiplier: object) -> int:
    """Round physical work to seconds, independent of allowed-laytime rounding."""
    if type(work_minutes) is not int or work_minutes <= 0 or isinstance(multiplier, bool):
        raise ValueError('Invalid work duration input')
    try:
        factor = Decimal(str(multiplier))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('Invalid work duration multiplier') from exc
    if not factor.is_finite() or factor <= 0:
        raise ValueError('Work duration multiplier must be positive and finite')
    with localcontext() as ctx:
        ctx.prec = max(50, len(factor.as_tuple().digits) + len(str(work_minutes)) + 8)
        seconds = Decimal(work_minutes) * factor * 60
        rounded = int(seconds.to_integral_value(rounding=ROUND_HALF_UP))
    if rounded <= 0:
        raise ValueError('Work duration rounds below one physical second')
    return rounded


def remaining_work_kg(total_kg: int, total_seconds: int, worked_seconds: int) -> int:
    """Integer kg balance; completed kg plus remaining kg equals source kg."""
    if (any(type(value) is not int for value in (total_kg, total_seconds, worked_seconds))
            or total_kg < 0 or total_seconds <= 0
            or not 0 <= worked_seconds <= total_seconds):
        raise ValueError('Invalid cargo/work balance')
    left = total_seconds - worked_seconds
    return (total_kg * left + total_seconds - 1) // total_seconds


def worked_seconds_until(work_intervals: list, cut: datetime) -> int:
    target = kst_time(cut)
    return sum(max(0, int((min(kst_time(end), target) - kst_time(start)).total_seconds()))
               for start, end in work_intervals)


def _append(intervals: list, start: datetime, end: datetime) -> None:
    if end <= start:
        return
    if intervals and intervals[-1][1] == start:
        intervals[-1] = (intervals[-1][0], end)
    else:
        intervals.append((start, end))


def work_timeline(start: datetime, seconds: int, weather, *, daylight: bool) -> dict:
    """Consume exact physical seconds; weather changes only at its source slots."""
    if type(seconds) is not int or seconds <= 0 or start.microsecond:
        raise ValueError('Work timeline requires positive whole seconds')
    at = start
    remaining = seconds
    work, weather_stops, night_stops = [], [], []
    while remaining:
        if daylight:
            sunrise, sunset = _risk_daylight_window(at)
            if at < sunrise or at >= sunset:
                next_start = (sunrise if at < sunrise else
                              _risk_daylight_window(datetime.combine(
                                  at.date() + timedelta(days=1), datetime.min.time()))[0])
                _append(night_stops, at, next_start)
                at = next_start
                continue
        _, weather_end, _ = weather.slot(at, RULE)
        end = min(weather_end, sunset) if daylight else weather_end
        if weather.blocked(at, RULE):
            _append(weather_stops, at, end)
            at = end
            continue
        consumed = min(remaining, int((end - at).total_seconds()))
        if consumed <= 0:
            raise ValueError('No forward progress in physical work')
        finish = at + timedelta(seconds=consumed)
        _append(work, at, finish)
        at, remaining = finish, remaining - consumed
    return dict(work_start_at=start.isoformat(), completion_at=at.isoformat(),
                work_intervals=[[a.isoformat(), b.isoformat()] for a, b in work],
                weather_pause_intervals=[[a.isoformat(), b.isoformat()] for a, b in weather_stops],
                night_pause_intervals=[[a.isoformat(), b.isoformat()] for a, b in night_stops])


def first_workable_start(proposed: datetime, weather, *, daylight: bool) -> datetime:
    """Keep a safe second inside its weather slot; advance only when blocked."""
    at = proposed
    while True:
        if daylight:
            sunrise, sunset = _risk_daylight_window(at)
            if at < sunrise:
                at = sunrise
            if at >= sunset:
                at = datetime.combine(at.date() + timedelta(days=1), datetime.min.time())
                continue
        workable = weather.first_workable(at, RULE)
        if not daylight or workable < sunset:
            return workable
        at = datetime.combine(workable.date() + timedelta(days=1), datetime.min.time())


def berth_overlap(left: dict, right: dict) -> bool:
    """Compare half-open second-precision occupancy intervals on one berth."""
    if left['berth_id'] != right['berth_id']:
        return False
    def bounds(row):
        start = kst_time(row.get('berthing_start_at', row['work_start_at']))
        end = kst_time(row.get('berth_release_at', row['completion_at']))
        if start.microsecond or end.microsecond or end <= start:
            raise ValueError('Invalid second-precision berth occupancy')
        return start, end
    a, b = bounds(left)
    c, d = bounds(right)
    return max(a, c) < min(b, d)
