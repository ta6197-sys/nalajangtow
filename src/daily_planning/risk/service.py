"""Stage orchestration: stages 1+statistics are ready; replay is injected.

Do not use dummy cost outputs with real runs. No DB mutation by design.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal

from .candidate_reader import load_run_candidates
from .metrics import compare_scenario_costs
from .metrics import risk_statistics
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
    if not scenarios:
        raise ValueError('At least one scenario required')
    costs = {}
    failures = []
    outcomes = {}
    vessel_costs = {}
    for candidate in candidates:
        label = candidate["label"]
        if label in costs:
            raise ValueError("Duplicate candidate label")
        values = {}
        seen = set()
        outcomes[label] = {}
        vessel_costs[label] = {}
        for scenario in scenarios:
            sid = scenario["scenario_id"]
            if sid in seen:
                raise ValueError("Duplicate scenario")
            seen.add(sid)
            outcome = evaluator(candidate, scenario)
            if isinstance(outcome, Mapping):
                outcomes[label][sid] = dict(outcome)
                if outcome.get('status') == 'FAILED':
                    failures.append(dict(candidate=label, scenario_id=sid,
                                         reason=outcome.get('failure_reason', 'Unspecified replay failure'),
                                         physical_replay=outcome.get('physical_replay')))
                    continue
                if outcome.get('status') != 'SUCCEEDED':
                    raise ValueError(f'Invalid replay status: {label}/{sid}')
                cost = outcome.get('cost_cents')
                if type(cost) is not int or cost < 0:
                    raise ValueError('Evaluator must return validated integer cents')
                if 'vessel_results' in outcome:
                    vessels = outcome['vessel_results']
                    vessel_costs[label][sid] = {
                        row['call_id']: row['demurrage_increment_to_completion_cents']
                        for row in vessels}
                    if (len(vessel_costs[label][sid]) != len(vessels)
                            or sum(vessel_costs[label][sid].values()) != cost):
                        raise ValueError(f'Vessel costs do not reconcile: {label}/{sid}')
            else:
                cost = outcome
            if type(cost) is not int or cost < 0:
                raise ValueError("Evaluator must return validated integer cents")
            values[sid] = cost
        costs[label] = values
    count = len(scenarios)
    failure_rates = {}
    for label in costs:
        failed = sum(item['candidate'] == label for item in failures)
        failure_rates[label] = dict(failed_count=failed, scenario_count=count,
            failure_probability=format((Decimal(failed) / Decimal(count)).quantize(
                Decimal('0.000001')), 'f'))
    if failures:
        return dict(risk_status='FAILED_SCENARIOS', failures=failures,
                    failed_count=len(failures), scenario_count=len(scenarios),
                    failure_rates_by_candidate=failure_rates, risk_by_candidate=None,
                    scenario_results=outcomes, vessel_loss_by_candidate=None)
    weights = {s["scenario_id"]: s["scenario_weight"] for s in scenarios}
    report = compare_scenario_costs(costs,weights_by_scenario=weights,baseline=baseline,alpha=alpha)
    report['risk_status'] = 'SUCCEEDED'
    report['failure_rates_by_candidate'] = failure_rates
    report['failures'] = []
    report['scenario_results'] = outcomes
    report['vessel_loss_by_candidate'] = None
    if all(len(vessel_costs[label]) == count for label in costs):
        reference = vessel_costs[baseline]
        labels = {}
        for label in costs:
            calls = set(vessel_costs[label][next(iter(weights))])
            if any(set(vessel_costs[label][sid]) != calls or set(reference[sid]) != calls
                   for sid in weights):
                raise ValueError('Vessel IDs differ across paired scenario results')
            labels[label] = {cid: dict(
                cost_risk=risk_statistics(
                    [vessel_costs[label][sid][cid] for sid in weights],
                    [weights[sid] for sid in weights], alpha=alpha),
                paired_loss_risk=risk_statistics(
                    [vessel_costs[label][sid][cid] - reference[sid][cid]
                     for sid in weights],
                    [weights[sid] for sid in weights], alpha=alpha))
                for cid in sorted(calls)}
        report['vessel_loss_by_candidate'] = labels
    return report


def evaluate_monte_carlo(snapshot: Mapping, candidates: list[dict], config: Mapping) -> dict:
    """One seeded scenario draw shared by A/B/C; no database mutation."""
    from .replay import evaluate_scenario
    if {candidate.get('label') for candidate in candidates} != {'A', 'B', 'C'} or len(candidates) != 3:
        raise ValueError('Monte Carlo evaluation requires exactly A/B/C')
    call_ids = [row['call_id'] for row in candidates[0]['vessel_results']]
    if any({row['call_id'] for row in candidate['vessel_results']} != set(call_ids)
           for candidate in candidates):
        raise ValueError('Candidate vessel sets differ')
    scenarios = generate_scenarios(config, call_ids)
    report = evaluate_with_adapter(candidates, scenarios,
        lambda candidate, scenario: evaluate_scenario(snapshot, candidate, scenario))
    return dict(report, scenarios=scenarios, model_id=config['model_id'],
                seed=config['seed'], input_snapshot_hash=snapshot['snapshot_hash'])
