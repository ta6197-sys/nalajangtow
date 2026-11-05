"""Stage 1: exact weighted discrete VaR/CVaR for evaluated scenario costs.

Uses integer cents and Decimal probabilities. No portfolio/financial assumptions.
Risk metrics do NOT validate whether the underlying schedule replay is correct.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from collections.abc import Mapping, Sequence


DISPLAY_PRECISION = Decimal("0.000001")  # fractional cents in expectations


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Boolean is not a probability")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal: {value}") from exc
    if not result.is_finite():
        raise ValueError("Nonfinite values are forbidden")
    return result


def _weights(n: int, weights: Sequence[object] | None) -> list[Decimal]:
    if n < 1:
        raise ValueError("At least one scenario is required")
    if weights is None:
        return [Decimal(1) / Decimal(n)] * n
    if len(weights) != n:
        raise ValueError("Weight and scenario counts differ")
    amounts = [_decimal(w) for w in weights]
    if any(w < 0 for w in amounts):
        raise ValueError("Negative scenario weight")
    total = sum(amounts)
    if total <= 0 or abs(total - 1) > Decimal("0.000000001"):
        raise ValueError(f"Scenario weights must sum to 1 (got {total})")
    return [w / total for w in amounts]


def _values(values: Sequence[object]) -> list[int]:
    if not values:
        raise ValueError("Empty scenario cost vector")
    if any(type(v) is not int for v in values):
        raise ValueError("Costs must be integer cents (not float/bool)")
    return list(values)


def _rounded_string(x: Decimal) -> str:
    return format(x.quantize(DISPLAY_PRECISION), "f")


def risk_statistics(
    costs_cents: Sequence[int],
    weights: Sequence[object] | None = None,
    *,
    alpha: object = "0.95",
) -> dict:
    """Weighted discrete cost/loss risk measures. Signed values are supported.

    CVaR is the average of the upper (1-alpha) probability tail, with
    fractional weight at the VaR boundary. Distinct from the naively filtered
    mean(cost >= VaR), which is incorrect when values have ties.

    Returned mean/CVaR are *decimal strings* in cents to preserve precision.
    """
    values = _values(costs_cents)
    level = _decimal(alpha)
    if not Decimal(0) < level < Decimal(1):
        raise ValueError("alpha must lie strictly between 0 and 1")
    with localcontext() as ctx:
        ctx.prec = 40
        probs = _weights(len(values), weights)
        pairs = sorted(zip(values, probs), key=lambda pair: pair[0])
        cumulative = Decimal(0)
        var = None
        for cost, prob in pairs:
            cumulative += prob
            if cumulative >= level:
                var = cost
                break
        if var is None:
            raise AssertionError("Invalid probabilities")
        tail = 1 - level
        remaining = tail
        tail_value = Decimal(0)
        for cost, prob in reversed(pairs):
            part = min(prob, remaining)
            if part > 0:
                tail_value += Decimal(cost) * part
                remaining -= part
            if remaining <= 0:
                break
        if remaining > Decimal("1e-20"):
            raise AssertionError("Tail probability not fully assigned")
        mean = sum(Decimal(cost) * prob for cost, prob in pairs)
        cvar = tail_value / tail
        return {
            "sample_count": len(values),
            "alpha": str(level),
            "expected_cost_cents": _rounded_string(mean),
            "var_cents": var,
            "cvar_cents": _rounded_string(cvar),
            "tail_premium_cents": _rounded_string(cvar - mean),
        }


def compare_scenario_costs(
    cost_by_candidate: Mapping[str, Mapping[str, int]],
    *,
    weights_by_scenario: Mapping[str, object] | None = None,
    baseline: str = "A",
    alpha: object = "0.95",
    provenance: str = "SYNTHETIC_ASSUMPTION",
) -> dict:
    """Aggregate common-scenario evaluated cost vectors.

    All candidates must have the exact same scenario IDs. Missing / invalid
    scenarios are an ERROR, never silently dropped or renormalized.
    This function requires actual replayed costs, not generated shocks alone.
    """
    if baseline not in cost_by_candidate:
        raise ValueError("Baseline candidate missing")
    if not cost_by_candidate:
        raise ValueError("No candidates")
    keys = sorted(cost_by_candidate[baseline])
    if not keys:
        raise ValueError("No evaluated scenarios")
    for candidate, costs in cost_by_candidate.items():
        if set(costs) != set(keys):
            raise ValueError(f"Scenario IDs differ for candidate {candidate}")
        for scenario, cost in costs.items():
            if type(cost) is not int or cost < 0:
                raise ValueError(f"Invalid total demurrage for {candidate}/{scenario}")
    if weights_by_scenario is not None and set(weights_by_scenario) != set(keys):
        raise ValueError("Scenario probability IDs differ")
    weights = _weights(len(keys), [weights_by_scenario[k] for k in keys] if weights_by_scenario else None)
    rows = {}
    for label, costs in sorted(cost_by_candidate.items()):
        amounts = [costs[k] for k in keys]
        summary = risk_statistics(amounts, weights, alpha=alpha)
        deltas = [costs[k] - cost_by_candidate[baseline][k] for k in keys]
        with localcontext() as ctx:
            ctx.prec = 40
            win = sum((w for w, d in zip(weights, deltas) if d < 0), Decimal(0))
            tie = sum((w for w, d in zip(weights, deltas) if d == 0), Decimal(0))
            expected_delta = sum((Decimal(d)*w for d,w in zip(deltas,weights)), Decimal(0))
        rows[label] = {
            **summary,
            "expected_delta_vs_baseline_cents": _rounded_string(expected_delta),
            "probability_cheaper_than_baseline": _rounded_string(win),
            "probability_equal_to_baseline": _rounded_string(tie),
            "paired_delta_cvar_cents": risk_statistics(deltas, weights, alpha=alpha)["cvar_cents"],
        }
    return {
        "baseline": baseline,
        "scenario_count": len(keys),
        "scenario_ids": keys,
        "provenance": provenance,
        "risk_by_candidate": rows,
    }
