"""Risk settlement gate around the unchanged Excel-driven minute contract engine."""
from __future__ import annotations

from datetime import datetime

from rolling_horizon import RULE
from weather_optimizer import price_contract

from ..bootstrap_db import kst_time


class UnresolvedSubminuteSettlement(ValueError):
    def __init__(self, physical_replay: list[dict]):
        super().__init__('UNRESOLVED_SUBMINUTE_SETTLEMENT_RULE')
        self.physical_replay = physical_replay


def require_minute_aligned(rows: list[dict]) -> None:
    """A physical second is retained, never rounded into a contract minute."""
    for row in rows:
        values = [row['work_start_at'], row['completion_at']]
        for key in ('weather_pause_intervals', 'night_pause_intervals'):
            values.extend(time for interval in row.get(key, []) for time in interval)
        if any(kst_time(value).second or kst_time(value).microsecond for value in values):
            raise UnresolvedSubminuteSettlement(rows)


def settle_minute_aligned(contract: dict, start: datetime, finish: datetime,
                          weather_pauses: list, weather):
    """Delegate whole-minute contract pricing without changing Excel rules."""
    rows = [dict(work_start_at=start, completion_at=finish,
                 weather_pause_intervals=weather_pauses)]
    require_minute_aligned(rows)
    return price_contract(contract, start, finish, weather_pauses, weather, RULE)
