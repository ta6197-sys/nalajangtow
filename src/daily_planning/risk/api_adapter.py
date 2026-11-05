"""Stage 2 API wiring helpers; web_app.py routing is intentionally untouched.

To connect: add GET /api/risk/tradeoff?run_id=<uuid> in Handler.do_GET
using urlparse/parse_qs, and call get_tradeoff_payload(ENGINE,run_id).
Do NOT attach to the POST mutate/confirm/operate actions.
"""
from __future__ import annotations

from .candidate_reader import load_run_candidates
from .tradeoff import compare_candidates


def get_tradeoff_payload(engine, run_id: str) -> dict:
    run = load_run_candidates(engine, run_id)
    return {"run_id": run["run_id"], "data_origin": "EXISTING_BASELINE_EVALUATION",
            **compare_candidates(run["candidates"])}


def get_risk_distribution_payload(engine, analysis_id: str) -> dict:
    """Requires a NEW persisted scenario-result model; not yet implemented."""
    raise NotImplementedError(
        "Wire to risk_analysis/risk_result tables only after migration and "
        "scenario replay tests pass. Never synthesize missing cost results."
    )
