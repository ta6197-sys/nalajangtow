"""Schedule preflight: cargo identity, single tank, and time/resource checks."""
from __future__ import annotations

from datetime import datetime
from fractions import Fraction

from .network import quantity, validate_network

SCHEDULE_VERSION = 'TANK_SCHEDULE_V1'
VALID_DIRECTIONS = {'DISCHARGE': 1, 'LOAD': -1}


def parse_time(value, field):
    if not isinstance(value, str):
        raise ValueError(f'{field}: timezone-aware ISO time required')
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f'{field}: invalid ISO timestamp') from exc
    if result.tzinfo is None or result.utcoffset() is None or result.microsecond:
        raise ValueError(f'{field}: timezone-aware, integer-second timestamp required')
    return result


def seconds_between(first, second):
    delta = second - first
    return delta.days * 86400 + delta.seconds


def issue(code, *, call_id=None, tank_id=None, reason=None):
    entry = {'code': code}
    if call_id is not None:
        entry['call_id'] = call_id
    if tank_id is not None:
        entry['tank_id'] = tank_id
    if reason is not None:
        entry['reason'] = reason
    return entry


def validate_schedule(network, schedule):
    """Return validation issues and normalized segments; never mutate inputs.

    Each call has ONE tank and a declared whole-cargo volume. Interruptions are
    represented as multiple non-overlapping transfer segments for the SAME call.
    """
    index = validate_network(network)
    if not isinstance(schedule, dict) or schedule.get('schema_version') != SCHEDULE_VERSION:
        raise ValueError('Unsupported tank schedule schema')
    start = parse_time(schedule.get('horizon_start_at'), 'horizon_start_at')
    end = parse_time(schedule.get('horizon_end_at'), 'horizon_end_at')
    if end <= start:
        raise ValueError('Schedule horizon must have positive length')
    calls = schedule.get('calls')
    segments = schedule.get('segments')
    if not isinstance(calls, list) or not isinstance(segments, list):
        raise ValueError('calls/segments arrays required')
    call_by_id = {}
    issues = []
    for call in calls:
        if not isinstance(call, dict):
            raise ValueError('Invalid call record')
        cid = call.get('call_id')
        if not isinstance(cid, str) or not cid or cid in call_by_id:
            raise ValueError('Missing or duplicate call_id')
        call_by_id[cid] = call
        tank = index['tank_by_id'].get(call.get('tank_id'))
        connection = index['connection_by_pair'].get((call.get('berth_id'), call.get('tank_id')))
        if tank is None:
            issues.append(issue('UNKNOWN_TANK', call_id=cid, tank_id=call.get('tank_id')))
        elif call.get('cargo_type') != tank.get('cargo_type'):
            issues.append(issue('TANK_CARGO_INCOMPATIBLE', call_id=cid, tank_id=call.get('tank_id')))
        if connection is None:
            issues.append(issue('TANK_CONNECTION_UNAVAILABLE', call_id=cid, tank_id=call.get('tank_id')))
        if call.get('direction') not in VALID_DIRECTIONS:
            issues.append(issue('UNKNOWN_CARGO_DIRECTION', call_id=cid))
        total = quantity(call.get('cargo_volume_bbl'), f'{cid}.cargo_volume_bbl')
        if total <= 0:
            raise ValueError(f'{cid}: cargo_volume_bbl must be positive')
        has_mass = 'cargo_mass_kg' in call
        has_density = 'density_kg_per_bbl' in call
        if has_mass != has_density:
            issues.append(issue('MISSING_MASS_CONVERSION', call_id=cid))
        elif has_mass:
            mass = quantity(call['cargo_mass_kg'], f'{cid}.cargo_mass_kg')
            density = quantity(call['density_kg_per_bbl'], f'{cid}.density_kg_per_bbl')
            if mass <= 0 or density <= 0 or total * density != mass:
                issues.append(issue('MASS_VOLUME_MISMATCH', call_id=cid))
    normalized = []
    transferred_by_id = {cid: Fraction(0) for cid in call_by_id}
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError('Invalid transfer segment')
        cid = segment.get('call_id')
        if cid not in call_by_id:
            issues.append(issue('UNKNOWN_SEGMENT_CALL', call_id=cid))
            continue
        begin = parse_time(segment.get('start_at'), f'{cid}.segment.start_at')
        finish = parse_time(segment.get('end_at'), f'{cid}.segment.end_at')
        if begin < start or finish > end or finish <= begin:
            issues.append(issue('TRANSFER_TIME_OUT_OF_RANGE', call_id=cid))
            continue
        rate = quantity(segment.get('rate_bbl_per_hour'), f'{cid}.rate_bbl_per_hour')
        if rate <= 0:
            issues.append(issue('NONPOSITIVE_TRANSFER_RATE', call_id=cid))
            continue
        call = call_by_id[cid]
        connection = index['connection_by_pair'].get((call.get('berth_id'), call.get('tank_id')))
        if connection and rate > quantity(connection['max_transfer_rate_bbl_per_hour'], 'max_transfer_rate_bbl_per_hour'):
            issues.append(issue('TRANSFER_RATE_EXCEEDED', call_id=cid, tank_id=call.get('tank_id')))
        duration_seconds = seconds_between(begin, finish)
        volume = rate * Fraction(duration_seconds, 3600)
        transferred_by_id[cid] += volume
        normalized.append({
            'call_id': cid, 'tank_id': call.get('tank_id'), 'berth_id': call.get('berth_id'),
            'direction': call.get('direction'), 'start': begin, 'end': finish,
            'rate': rate, 'volume': volume,
        })
    for cid, call in call_by_id.items():
        expected = quantity(call['cargo_volume_bbl'], f'{cid}.cargo_volume_bbl')
        if transferred_by_id[cid] != expected:
            issues.append(issue('CARGO_VOLUME_MISMATCH', call_id=cid, tank_id=call.get('tank_id'),
                                reason=f'expected {expected}, got {transferred_by_id[cid]} bbl'))
    ordered = sorted(normalized, key=lambda x: (x['start'], x['end'], x['call_id']))
    for pos, first in enumerate(ordered):
        for second in ordered[pos + 1:]:
            if second['start'] >= first['end']:
                break
            if first['call_id'] == second['call_id'] or first['tank_id'] == second['tank_id'] or first['berth_id'] == second['berth_id']:
                issues.append(issue('SHARED_RESOURCE_OVERLAP', call_id=second['call_id'],
                                    tank_id=second['tank_id'], reason=f'overlaps {first["call_id"]}'))
    return {'issues': issues, 'segments': normalized, 'calls': call_by_id,
            'start': start, 'end': end, 'index': index,
            'mass_verified_call_count': sum('cargo_mass_kg' in c and 'density_kg_per_bbl' in c for c in calls)}
