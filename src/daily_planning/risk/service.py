"""Stage orchestration: stages 1+statistics are ready; replay is injected.

Do not use dummy cost outputs with real runs. No DB mutation by design.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from .candidate_reader import load_run_candidates
from .metrics import compare_scenario_costs
from .scenarios import generate_scenarios
from .tradeoff import compare_candidates


def build_stage1_plan(engine, run_id: str, config: Mapping) -> dict:
    """Real DB candidates, valid trade-off and shocks; no risk numbers yet."""
    run = load_run_candidates(engine, run_id)
    tradeoff = compare_candidates(run["candidates"])
    call_ids = [r["call_id"] for r in run["candidates"][0]["vessel_results"]]
    scenarios = generate_scenarios(config, call_ids)
    return {
        "run_id": run["run_id"],
        "input_snapshot_hash": run["input_snapshot_hash"],
        "tradeoff": tradeoff,
        "scenarios": scenarios,
        "risk_status": "SCENARIOS_GENERATED_NOT_EVALUATED",
    }


def evaluate_with_adapter(
    candidates: list[dict],
    scenarios: list[dict],
    evaluator: Callable[[dict, dict], int | dict],
    *,
    baseline: str = "A",
    alpha: str = "0.95",
) -> dict:
    """Pure orchestration for a validated external scenario replay callback.

    The evaluator may return actual integer cents or a replay result. Any
    failed replay stops risk statistics and is retained with its reason.
    """
    costs = {}
    failures = []
    for candidate in candidates:
        label = candidate["label"]
        if label in costs:
            raise ValueError("Duplicate candidate label")
        values = {}
        for scenario in scenarios:
            sid = scenario["scenario_id"]
            if sid in values:
                raise ValueError("Duplicate scenario")
            outcome = evaluator(candidate, scenario)
            if isinstance(outcome, Mapping):
                if outcome.get('status') == 'FAILED':
                    failures.append(dict(candidate=label, scenario_id=sid,
                                         reason=outcome.get('failure_reason', 'Unspecified replay failure')))
                    continue
                if outcome.get('status') != 'SUCCEEDED':
                    raise ValueError(f'Invalid replay status: {label}/{sid}')
                cost = outcome.get('cost_cents')
            else:
                cost = outcome
            if type(cost) is not int or cost < 0:
                raise ValueError("Evaluator must return validated integer cents")
            values[sid] = cost
        costs[label] = values
    if failures:
        return dict(risk_status='FAILED_SCENARIOS', failures=failures,
                    failed_count=len(failures), scenario_count=len(scenarios),
                    risk_by_candidate=None)
    weights = {s["scenario_id"]: s["scenario_weight"] for s in scenarios}
    return compare_scenario_costs(costs,weights_by_scenario=weights,baseline=baseline,alpha=alpha)
