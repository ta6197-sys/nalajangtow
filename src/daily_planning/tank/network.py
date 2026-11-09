"""Strict, read-only schema checks for a tank/berth network.

Volumes use barrels. Rates use barrels/hour. JSON quantities must be decimal
strings or integers, not Python floats; this prevents implicit float rounding.
No berth is deemed physically compatible unless explicitly connected here.
"""
from __future__ import annotations

from fractions import Fraction
import math

NETWORK_VERSION = 'TANK_NETWORK_V1'


def quantity(value, name):
    """Parse an exact rational from a decimal JSON string/integer."""
    if isinstance(value, (bool, float)) or value is None:
        raise ValueError(f'{name}: expected a decimal string or integer (no float/bool)')
    try:
        result = Fraction(str(value))
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError(f'{name}: not an exact numeric quantity') from exc
    if not math.isfinite(float(result)):
        raise ValueError(f'{name}: invalid magnitude')
    return result


def validate_network(network):
    if not isinstance(network, dict) or network.get('schema_version') != NETWORK_VERSION:
        raise ValueError('Unsupported tank network schema_version')
    if network.get('data_origin') not in ('SYNTHETIC_FIXTURE', 'SOURCE_VERIFIED'):
        raise ValueError('Unknown tank network data_origin')
    tanks = network.get('tanks')
    connections = network.get('connections')
    if not isinstance(tanks, list) or not isinstance(connections, list) or not tanks:
        raise ValueError('Tanks and connections must be arrays')
    tank_by_id = {}
    for row in tanks:
        if not isinstance(row, dict):
            raise ValueError('Invalid tank row')
        tank_id = row.get('tank_id')
        cargo_type = row.get('cargo_type')
        if not isinstance(tank_id, str) or not tank_id or tank_id in tank_by_id:
            raise ValueError('Missing or duplicate tank ID')
        if not isinstance(cargo_type, str) or not cargo_type:
            raise ValueError(f'{tank_id}: cargo_type missing')
        cap = quantity(row.get('capacity_bbl'), f'{tank_id}.capacity_bbl')
        lo = quantity(row.get('min_operating_bbl'), f'{tank_id}.min_operating_bbl')
        hi = quantity(row.get('max_operating_bbl'), f'{tank_id}.max_operating_bbl')
        opening = quantity(row.get('initial_stock_bbl'), f'{tank_id}.initial_stock_bbl')
        quantity(row.get('fixed_process_net_flow_bbl_per_hour'), f'{tank_id}.fixed_process_net_flow_bbl_per_hour')
        if not 0 <= lo <= opening <= hi <= cap or cap <= 0:
            raise ValueError(f'{tank_id}: inconsistent capacity/operating/initial inventory')
        tank_by_id[tank_id] = row
    required_count = network.get('required_tank_count')
    if not isinstance(required_count, int) or isinstance(required_count, bool) or required_count != 30 or len(tanks) != required_count:
        raise ValueError('V2 network requires exactly 30 physical tanks')
    total_cap = quantity(network.get('required_total_capacity_bbl'), 'required_total_capacity_bbl')
    if total_cap != 10_000_000 or sum((quantity(t['capacity_bbl'], 'capacity') for t in tanks), Fraction(0)) != total_cap:
        raise ValueError('V2 network requires exactly 10,000,000 bbl total physical capacity')
    seen_connections = set()
    for row in connections:
        if not isinstance(row, dict):
            raise ValueError('Invalid connection row')
        berth_id, tank_id = row.get('berth_id'), row.get('tank_id')
        if not isinstance(berth_id, str) or not berth_id or tank_id not in tank_by_id:
            raise ValueError('Unknown berth or tank connection')
        if (berth_id, tank_id) in seen_connections:
            raise ValueError('Duplicate berth–tank connection')
        seen_connections.add((berth_id, tank_id))
        rate = quantity(row.get('max_transfer_rate_bbl_per_hour'), 'max_transfer_rate_bbl_per_hour')
        if rate <= 0:
            raise ValueError('Connection max transfer rate must be positive')
    if network['data_origin'] == 'SOURCE_VERIFIED' and not network.get('source_reference'):
        raise ValueError('Verified network requires an explicit source_reference')
    return {
        'tank_by_id': tank_by_id,
        'connection_by_pair': {(c['berth_id'], c['tank_id']): c for c in connections},
    }
