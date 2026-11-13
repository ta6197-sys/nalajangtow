"""Versioned V5 BALANCED inventory policy. No source mutation or solver decisions.

Physical stock range: [0, capacity]. Operating interval: [source minimum,
source effective maximum]. Fractions preserve exact barrel arithmetic.
Legacy V5_3TANK tanks without the policy marker retain their existing bounds.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Mapping

from ..integrated_input.validator import number

POLICY_VERSION = 'V5_PHYSICAL_BOUNDS_OPERATING_WARNINGS_V1'


def policy_view(snapshot: Mapping, version: str | None) -> dict:
    """Make a policy-specific working copy without changing source fingerprints."""
    from copy import deepcopy
    if version is None:
        return deepcopy(snapshot)
    if version != POLICY_VERSION or snapshot.get('scenario_id') != 'V5_BALANCED_154':
        raise ValueError('Unsupported inventory policy or scenario')
    result = deepcopy(snapshot)
    from ..integrated_input.provenance import canonical, digest
    result['_verified_source_fingerprint'] = digest(canonical(snapshot).encode())
    for tank in result['tanks']:
        tank['inventory_policy_version'] = version
        tank['operational_min_inventory_bbl'] = tank['min_inventory_bbl']
        tank['operational_max_inventory_bbl'] = tank['effective_max_inventory_bbl']
        operating_bounds(tank)
    return result


def _num(value) -> Fraction:
    if value is None or isinstance(value, bool):
        raise ValueError('Inventory bound/quantity is missing or boolean')
    return number(value)


def physical_bounds(tank: Mapping) -> tuple[Fraction, Fraction]:
    """Return hard bounds. Reject unrecognized versions instead of bypassing."""
    version = tank.get('inventory_policy_version')
    if version is None:
        return _num(tank['min_inventory_bbl']), _num(tank.get('effective_max_inventory_bbl', tank['capacity_bbl']))
    if version != POLICY_VERSION:
        raise ValueError('Unknown inventory policy: ' + str(version))
    capacity = _num(tank['capacity_bbl'])
    operating_bounds(tank)  # fail closed on malformed operating thresholds
    return Fraction(0), capacity


def operating_bounds(tank: Mapping) -> tuple[Fraction, Fraction]:
    if tank.get('inventory_policy_version') != POLICY_VERSION:
        raise ValueError('Versioned operating bounds required')
    minimum = _num(tank['operational_min_inventory_bbl'])
    maximum = _num(tank['operational_max_inventory_bbl'])
    capacity = _num(tank['capacity_bbl'])
    if not Fraction(0) <= minimum <= maximum <= capacity or capacity <= 0:
        raise ValueError('Invalid physical/operating inventory thresholds')
    if minimum != _num(tank['min_inventory_bbl']):
        raise ValueError('Operating minimum differs from source tank minimum')
    if maximum != _num(tank['effective_max_inventory_bbl']):
        raise ValueError('Operating maximum differs from source effective maximum')
    return minimum, maximum


def operating_warning_summary(tanks: list[Mapping], inventory_series: Mapping) -> dict:
    """Summarize warning-range excursions without changing feasibility.

    Reads the exact validated point series; duration is intentionally omitted
    because event-sampled warning points do not certify threshold-crossing times.
    Physical violations are reported as errors by the independent replay,
    never demoted here.
    """
    warnings = []
    enabled = [t for t in tanks if t.get('inventory_policy_version') == POLICY_VERSION]
    for tank in enabled:
        tid = tank['tank_id']
        lo, hi = operating_bounds(tank)
        hard_lo, hard_hi = physical_bounds(tank)
        rows = inventory_series.get(tid)
        if not isinstance(rows, list) or not rows:
            raise ValueError('Missing inventory point series for ' + tid)
        values = []
        for row in rows:
            at = row['at']
            value = _num(row['inventory_bbl'])
            if value < hard_lo or value > hard_hi:
                raise ValueError('Physical inventory violation cannot be treated as warning: ' + tid)
            values.append((at, value))
        min_at, minimum = min(values, key=lambda p: p[1])
        max_at, maximum = max(values, key=lambda p: p[1])
        if minimum < lo:
            warnings.append({
                'code': 'OPERATIONAL_MINIMUM_BREACH', 'tank_id': tid,
                'at': min_at, 'observed_bbl': str(minimum),
                'threshold_bbl': str(lo), 'shortfall_bbl': str(lo - minimum),
                'physical_feasibility_unchanged': True,
            })
        if maximum > hi:
            warnings.append({
                'code': 'OPERATIONAL_MAXIMUM_BREACH', 'tank_id': tid,
                'at': max_at, 'observed_bbl': str(maximum),
                'threshold_bbl': str(hi), 'excess_bbl': str(maximum - hi),
                'physical_feasibility_unchanged': True,
            })
    return {
        'inventory_policy_version': POLICY_VERSION if enabled else None,
        'operational_warning_count': len(warnings),
        'operational_warnings': warnings,
        'operational_warning_scope': 'VALIDATED_EVENT_POINTS_ONLY_NOT_EXACT_DURATION',
    }
