"""Stage 4 design hooks — no MAPS/EMA dependencies imported at startup.

Keep Docktimize's contract calculator as the financial source of truth.
"""
from __future__ import annotations

from typing import Protocol, Mapping, Any


class ExternalResourceSimulator(Protocol):
    """Future MAPS-style adapter; snapshots and contracts remain internal."""

    def simulate_resources(self, *, scenario: Mapping[str, Any],
                           candidate: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return berth/tank/pipeline/tug event intervals, not final charges."""
        ...


def evaluate_ema_policy(*, policy_id: str, uncertainties: Mapping[str, float]) -> dict:
    """Future EMA Workbench model callback; not connected to production replay."""
    raise NotImplementedError(
        "After validating Stage 2 replay, map EMA policy/scenario inputs to "
        "the same replay evaluator. Do not duplicate demurrage settlement."
    )
