"""Simulation-only second-precision laytime; original contracts.py is untouched.

The V1 exhaustion grace ends at the next 60-second boundary anchored at
contract-effective laytime commencement. This is an assumption for sub-minute
scenarios, not a verified contractual clause. Whole-minute inputs delegate to
the original calculator, preserving its labels and edge behavior exactly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, time
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING

from contracts import (EVENT_CODES, RULE_VERSION,
                       day_credit, settle_intervals, validate_contract)
from rolling_horizon import RULE


POLICY_VERSION = 'SECOND_PRECISION_SIMULATION_V1'
ASSUMPTION_VERSION = 'EFFECTIVE_START_ANCHORED_60_SECOND_EXHAUSTION_V1'
KST = timezone(timedelta(hours=9))


def second_time(value: datetime | str) -> datetime:
    at = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(at, datetime):
        raise ValueError('Contract time must be datetime or ISO string')
    if at.tzinfo is not None:
        at = at.astimezone(KST).replace(tzinfo=None)
    if at.microsecond:
        raise ValueError('Second-precision settlement requires integer seconds')
    return at


def _delta_seconds(delta: timedelta) -> Decimal:
    return (Decimal(delta.days * 86400 + delta.seconds)
            + Decimal(delta.microseconds) / Decimal(1000000))


def _after_seconds(at: datetime, seconds: Decimal) -> datetime:
    micros = seconds * 1000000
    if micros != micros.to_integral_value():
        raise ValueError('Sub-microsecond accounting boundary is unsupported')
    return at + timedelta(microseconds=int(micros))


def _onset_boundary(effective: datetime, exhausted: datetime) -> datetime:
    elapsed = _delta_seconds(exhausted - effective)
    if elapsed < 0:
        raise ValueError('Laytime exhaustion precedes effective start')
    ticks = int((elapsed / 60).to_integral_value(rounding=ROUND_CEILING))
    return effective + timedelta(minutes=ticks)


def _calendar_boundary_seconds(at: datetime, mode: str) -> datetime:
    midnight = datetime.combine(at.date()+timedelta(days=1), time.min)
    if mode == 'SHEX_SAT12' and at.weekday() == 5 and at.hour < 12:
        return min(midnight, datetime.combine(at.date(), time(hour=12)))
    return midnight


def contract_clock_start_seconds(contract: dict, work_start: datetime | str) -> datetime:
    validate_contract(contract)
    nor = second_time(contract['nor_at'])
    notice = Decimal(str(contract['notice_hours'])) * 60
    if notice != notice.to_integral_value():
        raise ValueError('Notice period must resolve to whole minutes')
    start = nor + timedelta(minutes=int(notice))
    if contract['early_start_if_work'] == 'Y':
        start = min(start, max(nor, second_time(work_start)))
    return start


def _is_post_excluded(code: str, post: str) -> bool:
    return ((code == 'OWNER_BREAKDOWN' and post in {'OWNER_ONLY', 'OWNER_AND_TERMINAL'})
            or (code == 'TERMINAL_STOP' and post == 'OWNER_AND_TERMINAL'))


def _minute_aligned(start: datetime, finish: datetime, events: list) -> bool:
    return (not start.second and not finish.second and
            all(not at.second for a,b,_ in events for at in (a,b)))


def settle_intervals_seconds(contract: dict, clock_start: datetime | str,
                             finish: datetime | str, intervals: list) -> dict:
    """Settle an exhaustive event timeline using exact seconds and Decimal credit."""
    validate_contract(contract)
    start, finish = second_time(clock_start), second_time(finish)
    events = sorted(((second_time(a), second_time(b), code) for a,b,code in intervals),
                    key=lambda item: item[0])
    if _minute_aligned(start, finish, events):
        original = settle_intervals(contract, start, finish, events)
        return dict(original, settlement_policy_version=POLICY_VERSION,
            settlement_assumption_version=ASSUMPTION_VERSION,
            source_contract_rule_version=RULE_VERSION, settlement_precision='SECOND',
            simulation_only=True,
            laytime_used_seconds=str(Decimal(str(original['laytime_used_minutes']))*60),
            demurrage_seconds=str(Decimal(str(original['demurrage_minutes']))*60),
            pre_excluded_seconds=str(Decimal(str(original['pre_excluded_minutes']))*60),
            post_excluded_seconds=str(Decimal(str(original['post_excluded_minutes']))*60),
            unused_laytime_seconds=str(Decimal(str(original['unused_laytime_minutes']))*60))
    allowed = Decimal(str(contract['allowed_minutes'])) * 60
    remaining = allowed
    used = demurrage = pre_excluded = post_excluded = Decimal(0)
    onset = start if allowed == 0 else None
    holidays = contract.get('holidays', [])
    if not isinstance(holidays, list):
        raise ValueError('Contract holidays must be a list')
    holidays = set(str(day)[:10] for day in holidays)
    cursor = start
    for raw_start, raw_end, code in events:
        a, b = max(start, raw_start), min(finish, raw_end)
        if a >= b:
            continue
        if code not in EVENT_CODES:
            raise ValueError(f'Unknown event code: {code}')
        if a != cursor:
            raise ValueError(f'Event timeline has a gap or overlap at {cursor}')
        at = a
        while at < b:
            end = min(b, _calendar_boundary_seconds(at, contract['day_mode']))
            elapsed = _delta_seconds(end - at)
            if remaining <= 0:
                grace = Decimal(0)
                if onset is not None and at < onset:
                    grace = min(elapsed, _delta_seconds(onset - at))
                    pre_excluded += grace
                post_span = elapsed - grace
                if _is_post_excluded(code, contract['post_demurrage_exceptions']):
                    post_excluded += post_span
                else:
                    demurrage += post_span
            else:
                credit = day_credit(at, contract['day_mode'], holidays)
                if code in {'OWNER_BREAKDOWN', 'TERMINAL_STOP'}:
                    credit = Decimal(0)
                elif code == 'WEATHER':
                    credit *= {'COUNT': Decimal(1), 'EXCLUDE': Decimal(0),
                               'HALF': Decimal('0.5')}[contract['weather_policy']]
                if credit == 0:
                    pre_excluded += elapsed
                else:
                    consumption = min(elapsed, remaining / credit)
                    counted = min(remaining, consumption * credit)
                    remaining -= counted
                    used += counted
                    pre_excluded += consumption - counted
                    if remaining == 0:
                        exhausted = _after_seconds(at, consumption)
                        onset = _onset_boundary(start, exhausted)
                        grace = min(elapsed - consumption,
                                    max(Decimal(0), _delta_seconds(onset - exhausted)))
                        pre_excluded += grace
                        post_span = elapsed - consumption - grace
                        if _is_post_excluded(code, contract['post_demurrage_exceptions']):
                            post_excluded += post_span
                        else:
                            demurrage += post_span
            at = end
        cursor = b
    if finish > start and cursor != finish:
        raise ValueError(f'Event timeline does not cover contract finish: {cursor} != {finish}')
    elapsed_total = max(Decimal(0), _delta_seconds(finish-start))
    if used + demurrage + pre_excluded + post_excluded != elapsed_total:
        raise AssertionError('Second-precision contract ledger does not reconcile')
    daily = Decimal(str(contract['daily_demurrage_usd']))
    cents = int((demurrage * daily * 100 / 86400).quantize(Decimal(1),
                rounding=ROUND_HALF_UP))
    despatch = (int((remaining * daily * 100 / 172800).quantize(Decimal(1),
                rounding=ROUND_HALF_UP)) if contract['despatch_policy']=='HALF_WORKING_TIME' else 0)
    return dict(contract_rule_version=POLICY_VERSION,
        source_contract_rule_version=RULE_VERSION,
        settlement_policy_version=POLICY_VERSION,
        settlement_assumption_version=ASSUMPTION_VERSION,
        settlement_precision='SECOND', simulation_only=True,
        clock_start_at=start.isoformat(), allowed_minutes=int(Decimal(str(contract['allowed_minutes']))),
        laytime_used_minutes=float(used/60), demurrage_minutes=float(demurrage/60),
        pre_excluded_minutes=float(pre_excluded/60),
        post_excluded_minutes=float(post_excluded/60),
        unused_laytime_minutes=float(remaining/60),
        laytime_used_seconds=str(used), demurrage_seconds=str(demurrage),
        pre_excluded_seconds=str(pre_excluded), post_excluded_seconds=str(post_excluded),
        unused_laytime_seconds=str(remaining),
        first_demurrage_at=onset.isoformat() if demurrage > 0 and onset else None,
        demurrage_cents=cents, despatch_cents=despatch,
        net_owner_receivable_cents=cents-despatch)


def price_contract_seconds(contract: dict, work_start: datetime | str,
                           finish: datetime | str, weather_pauses: list,
                           weather, rule=RULE, exception_intervals=None) -> dict:
    """Build the same WAIT/WEATHER/exception timeline as legacy price_contract."""
    start, finish = second_time(work_start), second_time(finish)
    effective = contract_clock_start_seconds(contract, start)
    interruptions = []
    if rule.weather_scope == 'WORK_ONLY':
        interruptions.extend((second_time(a),second_time(b),'WEATHER')
                             for a,b in weather_pauses)
    elif rule.weather_scope == 'CLOCK_WINDOW':
        at = effective
        while at < finish:
            _, boundary, _ = weather.slot(at, rule)
            end = min(finish, boundary)
            if weather.blocked(at, rule):
                interruptions.append((at,end,'WEATHER'))
            at = end
    else:
        raise ValueError('Unsupported weather scope')
    for a,b,code in exception_intervals or []:
        if code not in {'OWNER_BREAKDOWN','TERMINAL_STOP'}:
            raise ValueError(f'Unsupported scenario interruption: {code}')
        interruptions.append((second_time(a),second_time(b),code))
    interruptions = sorted((max(a,effective), min(b,finish), code)
        for a,b,code in interruptions if b>a and b>effective and a<finish)
    events = []
    at = effective
    for a,b,code in interruptions:
        if a < at:
            raise ValueError('Overlapping scenario interruptions require explicit resolution')
        if at < a:
            events.append((at,a,'WAIT'))
        events.append((a,b,code))
        at = b
    if at < finish:
        events.append((at,finish,'WAIT'))
    return settle_intervals_seconds(contract,effective,finish,events)


def price_until_seconds(contract: dict, row: dict, cut: datetime | str, weather,
                        rule=RULE) -> dict:
    cut = second_time(cut)
    start = second_time(row['work_start_at'])
    if start >= cut:
        notice = Decimal(str(contract['notice_hours']))*60
        if notice != notice.to_integral_value():
            raise ValueError('Notice period must resolve to whole minutes')
        marker = second_time(contract['nor_at'])+timedelta(minutes=int(notice))
        return price_contract_seconds(contract,marker,cut,[],weather,rule,
            exception_intervals=row.get('exception_intervals'))
    return price_contract_seconds(contract,start,
        min(cut,second_time(row['completion_at'])),
        row.get('weather_pause_intervals',[]),weather,rule,
        exception_intervals=row.get('exception_intervals'))
