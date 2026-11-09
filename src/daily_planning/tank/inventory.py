"""Exact event-aware tank inventory simulation.

Time grids are for reporting only. Physical transfer events can occur at any
whole second. All internal inventory and mass-conversion checks use fractions,
not binary floats; no cargo or inventory rounding is performed.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, localcontext
from fractions import Fraction

from .network import quantity
from .validator import issue, seconds_between, validate_schedule


def exact_value(value):
    """Human-readable approximate decimal AND exact rational audit form."""
    with localcontext() as ctx:
        ctx.prec = 32
        approx = Decimal(value.numerator) / Decimal(value.denominator)
    return {'bbl': format(approx, 'f'), 'exact_bbl': str(value)}


def project_inventory(network, schedule, *, interval_minutes=30):
    """Read-only feasibility evaluation of an already proposed transfer plan.

    The 30-minute grid is supplemented with EVERY transfer start/end boundary.
    Fixed process flows are integrated continuously, even overnight. An
    operating capacity violation is detected at event as well as grid points.
    """
    if not isinstance(interval_minutes, int) or isinstance(interval_minutes, bool) or interval_minutes <= 0:
        raise ValueError('interval_minutes must be a positive integer')
    check = validate_schedule(network, schedule)
    issues = list(check['issues'])
    if issues:
        return {'status': 'INFEASIBLE', 'issues': issues, 'inventory_at_grid': {},
                'inventory_at_events': {}, 'read_only': True,
                'operational_approval': False,
                'mass_verified_call_count': check['mass_verified_call_count']}
    tanks = check['index']['tank_by_id']
    segments = check['segments']
    start, end = check['start'], check['end']
    grid = [start]
    while grid[-1] < end:
        grid.append(min(grid[-1] + timedelta(minutes=interval_minutes), end))
    events = set(grid)
    for s in segments:
        events.add(s['start'])
        events.add(s['end'])
    boundaries = sorted(events)
    grid_set = set(grid)
    current = {key: quantity(t['initial_stock_bbl'], f'{key}.initial_stock_bbl') for key, t in tanks.items()}
    observations = {key: [] for key in tanks}
    event_observations = {key: [] for key in tanks}
    
    def record(at):
        for tid, inventory in current.items():
            point = {'at': at.isoformat(), **exact_value(inventory)}
            event_observations[tid].append(point)
            if at in grid_set:
                observations[tid].append(point)
            tank = tanks[tid]
            lo = quantity(tank['min_operating_bbl'], f'{tid}.min_operating_bbl')
            hi = quantity(tank['max_operating_bbl'], f'{tid}.max_operating_bbl')
            if inventory < lo:
                issues.append(issue('TANK_STOCK_BELOW_MINIMUM', tank_id=tid, reason=f'{at.isoformat()}: {inventory} < {lo} bbl'))
            if inventory > hi:
                issues.append(issue('TANK_CAPACITY_EXCEEDED', tank_id=tid, reason=f'{at.isoformat()}: {inventory} > {hi} bbl'))

    record(start)
    for begin, finish in zip(boundaries, boundaries[1:]):
        duration = Fraction(seconds_between(begin, finish), 3600)
        for tid, tank in tanks.items():
            flow = quantity(tank['fixed_process_net_flow_bbl_per_hour'], f'{tid}.fixed_process_net_flow_bbl_per_hour')
            for s in segments:
                if s['tank_id'] == tid and s['start'] <= begin and finish <= s['end']:
                    flow += s['rate'] * (1 if s['direction'] == 'DISCHARGE' else -1)
            current[tid] += duration * flow
        record(finish)
    return {'status': 'FEASIBLE' if not issues else 'INFEASIBLE',
            'issues': issues, 'inventory_at_grid': observations,
            'inventory_at_events': event_observations,
            'grid_points_per_tank': len(grid),
            'mass_verified_call_count': check['mass_verified_call_count'],
            'read_only': True, 'operational_approval': False,
            'data_origin': network['data_origin'],
            'model_version': 'TANK_INVENTORY_EXACT_EVENTS_V1'}
