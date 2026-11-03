"""Supplied SYN-LAYTIME minute-boundary rules; not general legal interpretation."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

RULE_VERSION = 'SYN-LAYTIME-v1-minute-boundary'
DAY_MODES = {'SHINC', 'SHEX_SUN', 'SHEX_WEEKEND', 'SHEX_SAT12'}
WEATHER_POLICIES = {'COUNT', 'EXCLUDE', 'HALF'}
POST_POLICIES = {'NONE', 'OWNER_ONLY', 'OWNER_AND_TERMINAL'}
EVENT_CODES = {'WORK', 'WAIT', 'WEATHER', 'PORT_CONGESTION', 'CHARTERER_WAIT', 'OWNER_BREAKDOWN', 'TERMINAL_STOP'}


def minute_time(value):
    at = datetime.fromisoformat(value) if isinstance(value, str) else value
    if at.tzinfo is not None:
        at = at.astimezone(timezone(timedelta(hours=9))).replace(tzinfo=None)
    if at.second or at.microsecond:
        raise ValueError('Contract times must be aligned to whole minutes')
    return at


def validate_contract(contract):
    if contract['nor_valid'] != 'Y':
        raise ValueError('Invalid NOR requires manual review')
    for field, allowed in [('day_mode', DAY_MODES), ('weather_policy', WEATHER_POLICIES),
                           ('post_demurrage_exceptions', POST_POLICIES),
                           ('early_start_if_work', {'Y', 'N'}),
                           ('despatch_policy', {'NONE', 'HALF_WORKING_TIME'})]:
        if contract[field] not in allowed:
            raise ValueError(f'Unsupported contract {field}: {contract[field]}')
    for field in ['allowed_minutes', 'daily_demurrage_usd', 'notice_hours']:
        value = Decimal(str(contract[field]))
        if not value.is_finite() or value < 0:
            raise ValueError(f'Invalid contract {field}')
    allowed = Decimal(str(contract['allowed_minutes']))
    if allowed != allowed.to_integral_value():
        raise ValueError('Allowed laytime must be an integer number of minutes')
    if Decimal(str(contract['notice_hours'])) * 60 != (Decimal(str(contract['notice_hours'])) * 60).to_integral_value():
        raise ValueError('Notice period must resolve to whole minutes')


def contract_clock_start(contract, work_start):
    validate_contract(contract)
    nor = minute_time(contract['nor_at'])
    start = nor + timedelta(minutes=int(Decimal(str(contract['notice_hours'])) * 60))
    if contract['early_start_if_work'] == 'Y':
        start = min(start, max(nor, minute_time(work_start)))
    return start


def day_credit(at, mode, holidays):
    if mode == 'SHINC':
        return Decimal(1)
    if at.date().isoformat() in holidays:
        return Decimal(0)
    if mode == 'SHEX_SUN':
        return Decimal(at.weekday() != 6)
    if mode == 'SHEX_WEEKEND':
        return Decimal(at.weekday() < 5)
    return Decimal(at.weekday() < 5 or (at.weekday() == 5 and at.hour < 12))


def calendar_boundary(at, mode):
    end = datetime.combine(at.date(), datetime.min.time()) + timedelta(days=1)
    if mode == 'SHEX_SAT12' and at.weekday() == 5 and at.hour < 12:
        end = min(end, at.replace(hour=12, minute=0))
    return end


def settle_intervals(contract, clock_start, finish, intervals):
    """Settle an explicitly covered event timeline, equivalent to minute replay.

    When laytime is exhausted within a minute, any fractional balance is counted
    in that minute and demurrage starts at the next minute, as in supplied labels.
    """
    validate_contract(contract)
    start, finish = minute_time(clock_start), minute_time(finish)
    allowed = Decimal(str(contract['allowed_minutes']))
    remaining = allowed
    used = demurrage = pre_excluded = post_excluded = Decimal(0)
    boundary = start if allowed == 0 else None
    holidays = contract.get('holidays', [])
    if not isinstance(holidays, list):
        raise ValueError('Contract holidays must be a list')
    holidays = set(str(x)[:10] for x in holidays)
    cursor = start
    for raw_start, raw_end, code in sorted(intervals, key=lambda item: minute_time(item[0])):
        a, b = max(start, minute_time(raw_start)), min(finish, minute_time(raw_end))
        if a >= b:
            continue
        if code not in EVENT_CODES:
            raise ValueError(f'Unknown event code: {code}')
        if a != cursor:
            raise ValueError(f'Event timeline has a gap or overlap at {cursor}')
        at = a
        while at < b:
            end = min(b, calendar_boundary(at, contract['day_mode']))
            elapsed = int((end - at).total_seconds() // 60)
            if remaining <= 0:
                post = contract['post_demurrage_exceptions']
                excluded = (code == 'OWNER_BREAKDOWN' and post in {'OWNER_ONLY', 'OWNER_AND_TERMINAL'}) or (code == 'TERMINAL_STOP' and post == 'OWNER_AND_TERMINAL')
                if excluded:
                    post_excluded += elapsed
                else:
                    demurrage += elapsed
            else:
                credit = day_credit(at, contract['day_mode'], holidays)
                if code in {'OWNER_BREAKDOWN', 'TERMINAL_STOP'}:
                    credit = Decimal(0)
                elif code == 'WEATHER':
                    credit *= {'COUNT': Decimal(1), 'EXCLUDE': Decimal(0), 'HALF': Decimal('0.5')}[contract['weather_policy']]
                if not credit:
                    pre_excluded += elapsed
                else:
                    until_exhausted = int((remaining / credit).to_integral_value(rounding=ROUND_CEILING))
                    prefix = min(elapsed, until_exhausted)
                    counted = min(remaining, Decimal(prefix) * credit)
                    remaining -= counted
                    used += counted
                    pre_excluded += Decimal(prefix) - counted
                    if remaining <= 0 and boundary is None:
                        boundary = at + timedelta(minutes=prefix)
                    demurrage += elapsed - prefix
            at = end
        cursor = b
    if finish > start and cursor != finish:
        raise ValueError(f'Event timeline does not cover contract finish: {cursor} != {finish}')
    elapsed = Decimal(max(0, int((finish - start).total_seconds() // 60)))
    assert used + demurrage + pre_excluded + post_excluded == elapsed
    daily = Decimal(str(contract['daily_demurrage_usd']))
    cents = int((demurrage * daily * 100 / 1440).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    despatch = int((remaining * daily * 100 / 2880).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if contract['despatch_policy'] == 'HALF_WORKING_TIME' else 0
    return {
        'contract_rule_version': RULE_VERSION, 'clock_start_at': start.isoformat(),
        'allowed_minutes': int(allowed), 'laytime_used_minutes': float(used),
        'demurrage_minutes': float(demurrage), 'pre_excluded_minutes': float(pre_excluded),
        'post_excluded_minutes': float(post_excluded), 'unused_laytime_minutes': float(remaining),
        'first_demurrage_at': boundary.isoformat() if demurrage > 0 else None,
        'demurrage_cents': cents, 'despatch_cents': despatch,
        'net_owner_receivable_cents': cents - despatch,
    }
