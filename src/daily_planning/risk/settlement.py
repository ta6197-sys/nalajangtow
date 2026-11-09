"""Strict legacy contract gate; minute rounding helper is sensitivity-only."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from rolling_horizon import RULE
from weather_optimizer import price_contract

from ..bootstrap_db import kst_time


class UnresolvedSubminuteSettlement(ValueError):
    def __init__(self, physical_replay: list[dict], boundaries: list[dict] | None = None):
        super().__init__('UNRESOLVED_SUBMINUTE_SETTLEMENT_RULE')
        self.physical_replay = physical_replay
        self.boundaries = boundaries or []


ROUNDING_MODES = frozenset({'FLOOR', 'CEILING', 'ROUND_HALF_UP'})


def _contract_boundaries(row: dict) -> list[tuple[str, datetime]]:
    """Only boundaries passed into the unchanged contract price calculator."""
    values = [('work_start_at', kst_time(row['work_start_at'])),
              ('completion_at', kst_time(row['completion_at']))]
    for index, (start, end) in enumerate(row.get('weather_pause_intervals', [])):
        values.extend(((f'weather_pause_intervals[{index}].start', kst_time(start)),
                       (f'weather_pause_intervals[{index}].end', kst_time(end))))
    for index, (start, end, _) in enumerate(row.get('exception_intervals', [])):
        values.extend(((f'exception_intervals[{index}].start', kst_time(start)),
                       (f'exception_intervals[{index}].end', kst_time(end))))
    return values


def subminute_contract_boundaries(rows: list[dict]) -> list[dict]:
    """Identify the exact accounting inputs that need a simulation assumption."""
    return [dict(call_id=row.get('call_id'), boundary=field, actual_at=at.isoformat(),
                 second=at.second)
            for row in rows for field, at in _contract_boundaries(row)
            if at.second or at.microsecond]


def project_minute_boundary(at: datetime, mode: str) -> datetime:
    """Isolated sensitivity helper; never used by Risk cost settlement."""
    if mode not in ROUNDING_MODES:
        raise ValueError('Unsupported simulation minute rounding mode')
    at = kst_time(at)
    if at.microsecond:
        raise ValueError('Physical replay must use integer-second timestamps')
    floor = at.replace(second=0)
    if mode == 'FLOOR' or at.second == 0:
        return floor
    if mode == 'CEILING' or at.second >= 30:
        return floor + timedelta(minutes=1)
    return floor


def contract_timeline(row: dict, physical_replay: list[dict]) -> dict:
    """Pass only minute-aligned contractual boundaries to legacy pricing."""
    boundaries = subminute_contract_boundaries([row])
    if boundaries:
        raise UnresolvedSubminuteSettlement(physical_replay, boundaries)
    start = kst_time(row['work_start_at'])
    finish = kst_time(row['completion_at'])
    pauses = [(kst_time(a), kst_time(b))
              for a,b in row.get('weather_pause_intervals', [])]
    exceptions = [(kst_time(a), kst_time(b), code)
                  for a,b,code in row.get('exception_intervals', [])]
    if finish < start:
        raise ValueError('Contract completion precedes start')
    return dict(start_at=start, completion_at=finish,
                weather_pause_intervals=pauses,
                exception_intervals=exceptions,
                policy_version='LEGACY_CONTRACT_PARITY', precision='MINUTE',
                simulation_only=False, adjustments=[])


def price_accounting_at(contract: dict, accounting: dict, cut: datetime, weather) -> dict:
    """Apply original notice, day, weather and exception rules at a minute cut."""
    cut = kst_time(cut)
    start = accounting['start_at']
    if start < cut:
        return price_contract(contract, start, min(cut, accounting['completion_at']),
            accounting['weather_pause_intervals'], weather, RULE,
            exception_intervals=accounting['exception_intervals'])
    notice = Decimal(str(contract['notice_hours'])) * 60
    if notice != notice.to_integral_value():
        raise ValueError('Notice period must resolve to whole minutes')
    marker = kst_time(contract['nor_at']) + timedelta(minutes=int(notice))
    return price_contract(contract, marker, cut, [], weather, RULE,
        exception_intervals=accounting['exception_intervals'])


def require_minute_aligned(rows: list[dict]) -> None:
    """Guard only inputs to minute-based contractual accounting."""
    boundaries = subminute_contract_boundaries(rows)
    if boundaries:
        raise UnresolvedSubminuteSettlement(rows, boundaries)


def settle_minute_aligned(contract: dict, start: datetime, finish: datetime,
                          weather_pauses: list, weather):
    """Delegate whole-minute contract pricing without changing Excel rules."""
    rows = [dict(work_start_at=start, completion_at=finish,
                 weather_pause_intervals=weather_pauses)]
    require_minute_aligned(rows)
    return price_contract(contract, start, finish, weather_pauses, weather, RULE)
