"""Opt-in V5 exact-cost canonical tie-break (no unproven cost tradeoffs).

For small finite ETB domains only. Reuses *the same* CP-SAT model, including
exact tank inventory, FIX, physical conflicts and no-goods. The primary cost
optimum must have been proven. All secondary ranks are exact small integers.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from time import perf_counter

MODE = 'CANONICAL_ETB_BERTH_V1'
MAX_GROUPS = 12  # S5/S10 study. Scaling to 140 requires a separate design.


def canonical_row_key(row):
    """Timezone-independent ordering, with a stable total tie breaker."""
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    etb = datetime.fromisoformat(row['etb_at'])
    if etb.tzinfo is None or etb.utcoffset() is None:
        raise ValueError('ETB must be timezone-aware')
    return (etb.astimezone(timezone.utc), str(row['berth_id']),
            str(row.get('hose_connection_start_at') or ''), canonical(row))


def schedule_key(rows):
    """Choose a consistent schedule among equal-cent validated incumbents."""
    return tuple((str(row['call_id']), canonical_row_key(row))
                 for row in sorted(rows, key=lambda r: str(r['call_id'])))


def option_ranks(options, groups):
    """Unique ranks within each exactly-one call group."""
    rankings = []
    for group in groups:
        ordered = sorted(group, key=lambda i: canonical_row_key(options[i]))
        rankings.append({index: rank for rank, index in enumerate(ordered)})
    return rankings


def stabilize_proven_cost(model, variables, options, groups, primary_expression,
                          primary_selection, primary_result, primary_cost_cents,
                          deadline, cp_model):
    """Return (selected_indices, metadata). Never change primary cost without proof.

    All secondary stages must return OPTIMAL. If any stage fails, preserve the
    pre-existing primary CP-SAT incumbent verbatim. No solver/model mutation is
    surfaced outside this call except added constraints and changed objective.
    """
    primary_selection = list(primary_selection)
    meta = {'mode': MODE, 'status': 'SKIPPED', 'primary_cost_cents': primary_cost_cents,
            'secondary_stages_proven': 0, 'secondary_stages_requested': len(groups),
            'primary_optimality_proven': primary_result == cp_model.OPTIMAL}
    if primary_result != cp_model.OPTIMAL:
        meta['status'] = 'SKIPPED_PRIMARY_UNPROVEN'
        return primary_selection, meta
    if type(primary_cost_cents) is not int or primary_cost_cents < 0:
        raise ValueError('Primary cost must be nonnegative exact integer cents')
    if len(groups) > MAX_GROUPS:
        meta['status'] = 'SKIPPED_SCOPE_OVER_12_CALLS'
        return primary_selection, meta
    if perf_counter() >= deadline:
        meta['status'] = 'SKIPPED_TIME_BUDGET'
        return primary_selection, meta
    if not groups:
        meta['status'] = 'NO_DECISION_GROUPS'
        return primary_selection, meta
    ranks = option_ranks(options, groups)
    model.Add(primary_expression == primary_cost_cents)
    last_selection = None
    for stage, group in enumerate(groups):
        seconds = deadline - perf_counter()
        if seconds <= 0:
            meta['status'] = 'INCOMPLETE_TIME_BUDGET_FALLBACK'
            return primary_selection, meta
        expr = sum(rankings * variables[i] for i, rankings in ranks[stage].items())
        model.Minimize(expr)
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 42
        solver.parameters.max_time_in_seconds = max(0.000001, seconds)
        status = solver.Solve(model)
        if status != cp_model.OPTIMAL:
            meta['status'] = 'INCOMPLETE_SECONDARY_NOT_PROVEN_FALLBACK'
            meta['secondary_stop_status'] = solver.StatusName(status)
            return primary_selection, meta
        rank_optimum = int(solver.Value(expr))
        model.Add(expr == rank_optimum)
        meta['secondary_stages_proven'] += 1
        last_selection = [i for i in range(len(options)) if solver.Value(variables[i])]
    if last_selection is None or len(last_selection) != len(groups):
        meta['status'] = 'INCOMPLETE_SELECTION_FALLBACK'
        return primary_selection, meta
    meta['status'] = 'PROVEN_CANONICAL_SELECTION'
    meta['selected_option_count'] = len(last_selection)
    return last_selection, meta
