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

    The evaluator may return actual integer cents or a replay result. Failed
    outcomes remain in coverage; full-distribution cost risk is unavailable.
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
                if outcome.get('status') in ('FAILED', 'MISSING'):
                    if outcome.get('cost_cents') is not None:
                        raise ValueError(f'Unpriced outcome has a cost: {label}/{sid}')
                    failure_class = outcome.get('failure_class')
                    if failure_class is None:
                        failure_class = ('MISSING' if outcome['status'] == 'MISSING' else
                                         'UNRESOLVED' if outcome.get('settlement_rule_status') else
                                         'INFEASIBLE')
                    if failure_class not in ('INFEASIBLE', 'UNRESOLVED', 'MISSING'):
                        raise ValueError(f'Invalid failure class: {label}/{sid}')
                    failures.append(dict(candidate=label, scenario_id=sid,
                                         reason=outcome.get('failure_reason', 'Unspecified replay failure'),
                                         failure_class=failure_class,
                                         reason_codes=outcome.get('reason_codes', []),
                                         affected_call_ids=outcome.get('affected_call_ids', []),
                                         settlement_boundaries=outcome.get('settlement_boundaries', []),
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
                if outcome is None:
                    outcomes[label][sid] = {'status': 'MISSING', 'cost_cents': None}
                    failures.append(dict(candidate=label, scenario_id=sid,
                                         reason='MISSING_OUTCOME', failure_class='MISSING',
                                         physical_replay=None))
                    continue
                cost = outcome
            if type(cost) is not int or cost < 0:
                raise ValueError("Evaluator must return validated integer cents")
            values[sid] = cost
        costs[label] = values
    count = len(scenarios)
    weights = {s["scenario_id"]: s["scenario_weight"] for s in scenarios}
    if len(weights) != count:
        raise ValueError('Duplicate scenario IDs')
    disruption = {}
    for label, rows in outcomes.items():
        total_weight = sum(Decimal(str(weights[sid])) for sid in rows)
        if not total_weight:
            disruption[label] = None
            continue
        disrupted = sum(Decimal(str(weights[sid])) for sid, outcome in rows.items()
            if outcome.get('commitment_status') == 'DISRUPTED')
        propagated = sum(Decimal(str(weights[sid])) for sid, outcome in rows.items()
            if outcome.get('propagated_delay_call_ids'))
        severity = sum(Decimal(str(weights[sid])) *
            Decimal(str(outcome.get('total_start_delay_minutes', 0)))
            for sid, outcome in rows.items())
        disruption[label] = dict(
            commitment_deviation_probability=format(disrupted/total_weight, '.6f'),
            propagated_delay_probability=format(propagated/total_weight, '.6f'),
            expected_total_start_delay_minutes=format(severity/total_weight, '.6f'),
            max_completion_delay_minutes=max((outcome.get('max_completion_delay_minutes', 0)
                for outcome in rows.values()), default=0))
    failure_breakdown = {}
    for label in costs:
        items = [item for item in failures if item['candidate'] == label]
        failure_breakdown[label] = {
            'by_class': {kind: sum(item['failure_class'] == kind for item in items)
                         for kind in ('INFEASIBLE', 'UNRESOLVED', 'MISSING')},
            'by_reason_code': {code: sum(code in item.get('reason_codes', []) for item in items)
                for code in sorted({code for item in items for code in item.get('reason_codes', [])})}}
    failure_rates, coverage = {}, {}
    for label in costs:
        category = {kind: sum(item['candidate'] == label and item['failure_class'] == kind
                              for item in failures)
                    for kind in ('UNRESOLVED', 'INFEASIBLE', 'MISSING')}
        settled = len(costs[label])
        physical = settled + category['UNRESOLVED']
        failed = count - settled
        coverage[label] = dict(physical_success=physical,
            settlement_success=settled, unresolved=category['UNRESOLVED'],
            infeasible=category['INFEASIBLE'], missing=category['MISSING'],
            scenario_count=count)
        if settled + sum(category.values()) != count:
            raise AssertionError(f'Outcome coverage does not reconcile: {label}')
        failure_rates[label] = dict(failed_count=failed, scenario_count=count,
            failure_probability=format((Decimal(failed) / Decimal(count)).quantize(
                Decimal('0.000001')), 'f'))
    if failures:
        conditional = {}
        for label, values in costs.items():
            ids = sorted(values)
            conditional[label] = (dict(risk_statistics(
                [values[sid] for sid in ids],
                [Decimal(str(weights[sid])) / sum(Decimal(str(weights[x])) for x in ids)
                 for sid in ids], alpha=alpha),
                conditioning='SETTLEMENT_SUCCEEDED_ONLY') if ids else None)
        common = sorted(set.intersection(*(set(values) for values in costs.values())))
        paired = None
        if common:
            total = sum(Decimal(str(weights[sid])) for sid in common)
            paired = compare_scenario_costs(
                {label: {sid: values[sid] for sid in common}
                 for label, values in costs.items()},
                weights_by_scenario={sid: str(Decimal(str(weights[sid])) / total)
                                     for sid in common}, baseline=baseline, alpha=alpha)
            paired['conditioning'] = 'ALL_CANDIDATES_SETTLEMENT_SUCCEEDED_ONLY'
        return dict(risk_status='FAILED_SCENARIOS', failures=failures,
                    failed_count=len(failures), scenario_count=len(scenarios),
                    failure_rates_by_candidate=failure_rates, risk_by_candidate=None,
                    full_distribution_available=False, coverage_by_candidate=coverage,
                    conditional_risk_by_candidate=conditional,
                    conditional_paired_risk=paired,
                    scenario_results=outcomes, vessel_loss_by_candidate=None,
                    disruption_by_candidate=disruption,
                    failure_breakdown_by_candidate=failure_breakdown)
    report = compare_scenario_costs(costs,weights_by_scenario=weights,baseline=baseline,alpha=alpha)
    report['risk_status'] = 'SUCCEEDED'
    report['failure_rates_by_candidate'] = failure_rates
    report['coverage_by_candidate'] = coverage
    report['full_distribution_available'] = True
    report['conditional_risk_by_candidate'] = None
    report['conditional_paired_risk'] = None
    report['failures'] = []
    report['scenario_results'] = outcomes
    report['disruption_by_candidate'] = disruption
    report['failure_breakdown_by_candidate'] = failure_breakdown
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


def evaluate_monte_carlo(snapshot: Mapping, candidates: list[dict], config: Mapping, *,
                         settlement_policy: str = 'LEGACY_CONTRACT_PARITY',
                         settlement_config: Mapping | None = None) -> dict:
    """One seeded scenario draw shared by A/B/C; no database mutation."""
    from .replay import (evaluate_scenario, LEGACY_CONTRACT_PARITY,
                         SECOND_PRECISION_SIMULATION_V1)
    from .golden_validation import validate_golden_workbook, validate_settlement_config
    golden = None
    if settlement_policy == SECOND_PRECISION_SIMULATION_V1:
        if settlement_config is None:
            raise ValueError('Second-precision policy requires an explicit versioned JSON configuration')
        settlement_config = validate_settlement_config(settlement_config)
        golden = validate_golden_workbook(settlement_config['golden_workbook_path'])
        if golden['source']['sha256'] != settlement_config['golden_workbook_sha256']:
            raise ValueError('Golden workbook hash differs from settlement configuration')
        if not golden['settlement_parity_complete']:
            raise ValueError('Golden settlement parity is incomplete; second-precision policy blocked')
        if golden['sof_event_count'] != settlement_config['golden_sof_event_count']:
            raise ValueError('Golden SOF count differs from settlement configuration')
        if golden['clock_start_divergence_count'] != settlement_config['expected_clock_start_divergences']:
            raise ValueError('Clock-start divergence count differs from audited configuration')
        if (golden['clock_start_divergence_count']
                and settlement_config['clock_start_divergence_policy'] == 'BLOCK'):
            raise ValueError('Source effective-start and NOR/notice rules diverge; policy blocked')
    elif settlement_policy != LEGACY_CONTRACT_PARITY or settlement_config is not None:
        raise ValueError('Unsupported settlement policy/configuration combination')
    if {candidate.get('label') for candidate in candidates} != {'A', 'B', 'C'} or len(candidates) != 3:
        raise ValueError('Monte Carlo evaluation requires exactly A/B/C')
    call_ids = [row['call_id'] for row in candidates[0]['vessel_results']]
    if any({row['call_id'] for row in candidate['vessel_results']} != set(call_ids)
           for candidate in candidates):
        raise ValueError('Candidate vessel sets differ')
    scenarios = generate_scenarios(config, call_ids)
    report = evaluate_with_adapter(candidates, scenarios,
        lambda candidate, scenario: evaluate_scenario(snapshot, candidate, scenario,
            settlement_policy=settlement_policy))
    return dict(report, scenarios=scenarios, model_id=config['model_id'],
                seed=config['seed'], input_snapshot_hash=snapshot['snapshot_hash'],
                settlement_policy_version=settlement_policy,
                settlement_precision=('SECOND' if settlement_policy ==
                    SECOND_PRECISION_SIMULATION_V1 else 'MINUTE'),
                settlement_assumptions=(dict(settlement_config) if settlement_config else None),
                settlement_simulation_only=settlement_policy == SECOND_PRECISION_SIMULATION_V1,
                golden_validation=golden)
