"""Stage 1: read-only adapter for current Docktimize PostgreSQL schema.

Imports of existing app modules are deferred until load_run_candidates is used,
so numeric unit tests work without Docker or PostgreSQL.
"""
from __future__ import annotations

import uuid


def normalize_db_candidate(row: dict, input_snapshot_hash: str) -> dict:
    """Map an existing schedule_candidate row to stable risk input schema."""
    metrics = row["evaluation_metrics"]
    chosen = metrics["comparison_evaluation_id"]
    evaluations = [e for e in metrics["evaluations"] if e["evaluation_id"] == chosen]
    if len(evaluations) != 1:
        raise ValueError("Missing/ambiguous comparison evaluation")
    ev = evaluations[0]
    if ev.get("case") != "BASELINE" or ev.get("status") != "SUCCEEDED":
        raise ValueError("Cannot compare unevaluated/nonbaseline candidate")
    if ev["input_snapshot_hash"] != input_snapshot_hash:
        raise ValueError("Immutable snapshot hash mismatch")
    cost = ev["summary"]["demurrage_increment_to_completion_cents"]
    if cost != row["simulated_demurrage_cents"]:
        raise ValueError("Stored candidate cost differs from exact evaluation")
    rank = row["candidate_rank"]
    if rank not in (1,2,3):
        raise ValueError("Stage 1 handles existing A/B/C ranks only")
    return {
        "label": "ABC"[rank-1],
        "candidate_id": str(row["candidate_id"]),
        "run_id": str(row["run_id"]),
        "strategy": row["strategy"],
        "input_snapshot_hash": input_snapshot_hash,
        "solver_status": metrics.get("solver", {}).get("solver_status", "UNKNOWN"),
        "solver_relative_gap": metrics.get("solver", {}).get("relative_gap"),
        "cost_cents": cost,
        "wait_minutes": ev["summary"]["total_wait_minutes"],
        "vessel_results": ev["vessel_results"],
        "schedule_signature": metrics["schedule_signature"],
        "freeze_signature": metrics["freeze_signature"],
    }


def load_run_candidates(engine, run_id: str) -> dict:
    """SELECT-only PostgreSQL read of planning_run and schedule_candidate.

    Call this within the existing Docker environment with SQLAlchemy installed.
    No schedule confirmation or operation advancement is invoked.
    """
    import sqlalchemy as sa
    from ..context import verify_snapshot
    from ..models import planning_run, schedule_candidate

    rid = uuid.UUID(str(run_id))
    with engine.connect() as conn:
        run = conn.execute(sa.select(planning_run).where(planning_run.c.run_id == rid)).mappings().one()
        rows = conn.execute(sa.select(schedule_candidate)
                            .where(schedule_candidate.c.run_id == rid)
                            .order_by(schedule_candidate.c.candidate_rank)).mappings().all()
    snapshot = run["run_config"]["input_snapshot"]
    verify_snapshot(snapshot)
    normalized = [normalize_db_candidate(dict(row), snapshot["snapshot_hash"]) for row in rows]
    if not normalized:
        raise ValueError("No A/B/C candidate has been generated for this run")
    return {
        "run_id": str(rid),
        "experiment_key": run["experiment_key"],
        "planning_as_of": run["planning_as_of"].isoformat(),
        "input_snapshot_hash": snapshot["snapshot_hash"],
        "candidates": normalized,
    }
