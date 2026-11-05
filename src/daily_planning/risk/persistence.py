"""Stage 2 PostgreSQL persistence boundary (intentionally unimplemented).

Codex follow-up:
- add Alembic migration for risk_analysis, risk_scenario, risk_result;
- FK to planning_run/schedule_candidate, unique scenario+candidate+mode;
- never write to source contract/port_call/call_state/schedule_commit;
- enforce analysis fingerprint, snapshot hash, model version, idempotent writes.
"""
from __future__ import annotations

from typing import Any, Mapping


def persist_risk_analysis(engine: Any, result: Mapping[str, Any]) -> str:
    """After DB migration, atomically persist validated outputs.

    This skeleton must fail explicitly rather than claim persistence.
    """
    raise NotImplementedError("Stage 2 pending: Alembic migration + transactional write tests")
