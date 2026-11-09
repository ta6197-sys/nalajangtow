"""Versioned rolling boundaries; no solver or persistence side effects."""
from dataclasses import dataclass
from datetime import timedelta

from .bootstrap_db import kst_time
from .context import local_time


@dataclass(frozen=True)
class HorizonPolicy:
    version: str
    forecast_hours: int
    optimization_hours: int
    freeze_hours: int
    step_hours: int
    inventory_interval_minutes: int

    def __post_init__(self):
        if not (self.forecast_hours >= self.optimization_hours >= self.freeze_hours > 0):
            raise ValueError('Invalid horizon ordering')
        if self.step_hours != self.freeze_hours or self.inventory_interval_minutes <= 0:
            raise ValueError('Invalid rolling step or inventory interval')

    def boundaries(self, as_of):
        local_time(as_of)  # Reject unsupported sub-minute input, as the scheduler does.
        start = kst_time(as_of)
        return {'as_of_at': start.isoformat(),
                'forecast_end_at': (start + timedelta(hours=self.forecast_hours)).isoformat(),
                'optimization_end_at': (start + timedelta(hours=self.optimization_hours)).isoformat(),
                'freeze_end_at': (start + timedelta(hours=self.freeze_hours)).isoformat(),
                'next_run_at': (start + timedelta(hours=self.step_hours)).isoformat()}


LEGACY_72H_V1 = HorizonPolicy('LEGACY_72H_V1', 72, 72, 24, 24, 30)
LOOKAHEAD_168H_V1 = HorizonPolicy('LOOKAHEAD_168H_V1', 168, 72, 24, 24, 30)
