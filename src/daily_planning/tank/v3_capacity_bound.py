"""Conservative exact single-tank discharge capacity bound, version 1.

This only eliminates an assignment if even an empty-to-minimum tank plus
all fixed external withdrawals during the transfer cannot fit the cargo.
Unknown data, another same-group vessel load, or incomplete flow coverage
turns the bound off for the affected choice. It is not an inventory validator.
"""
from fractions import Fraction
from ..integrated_input.validator import number, timestamp

VERSION = 'V3_SINGLE_TANK_CAPACITY_BOUND_V1'


def filter_tank_pairs(snapshot, transfers, pairs, *, carried_transfers=(), commitments=()):
    """Return retained (transfer-index, tank-id) pairs and the number removed."""
    try:
        calls = {c['call_id']: c for c in snapshot['calls']}
        tanks = {t['tank_id']: t for t in snapshot['tanks']}
        movements = list(transfers) + list(carried_transfers or ()) + list(commitments or ())
        carried_ids = {movement['call_id'] for movement in (carried_transfers or ())}
        # Unknown movement direction/group prevents proving the bound safely.
        load_groups = set()
        for movement in movements:
            call = calls[movement['call_id']]
            if call['direction'] not in ('LOAD', 'DISCHARGE') or not call.get('cargo_group'):
                raise ValueError('Unknown movement direction or cargo group')
            if call['direction'] == 'LOAD':
                load_groups.add(call['cargo_group'])
    except (KeyError, TypeError, ValueError):
        return list(pairs), 0

    retained = []
    removed = 0
    for i, tank_id in pairs:
        try:
            row, tank = transfers[i], tanks[tank_id]
            call = calls[row['call_id']]
            if call['direction'] != 'DISCHARGE' or call['cargo_group'] in load_groups or row['call_id'] in carried_ids:
                retained.append((i, tank_id)); continue
            start, end = timestamp(row['start_at']), timestamp(row['end_at'])
            if start >= end or start.microsecond or end.microsecond:
                raise ValueError('Invalid transfer interval')
            quantity = number(call['cargo_volume_bbl'])
            if quantity <= 0 or not row.get('segments'):
                raise ValueError('Missing exact transfer segments')
            transferred = Fraction(0)
            previous = start
            for segment in row['segments']:
                a, b = timestamp(segment['start_at']), timestamp(segment['end_at'])
                rate = number(segment['rate_bbl_per_hour'])
                if a < previous or a < start or b > end or b <= a or a.microsecond or b.microsecond or rate <= 0:
                    raise ValueError('Invalid transfer segment')
                transferred += rate * Fraction(int((b-a).total_seconds()), 3600)
                previous = b
            if transferred != quantity:
                raise ValueError('Transfer quantity not reconciled')
            cursor, withdrawn = start, Fraction(0)
            flows = []
            for flow in snapshot['fixed_process_flows']['segments']:
                if flow['tank_id'] != tank_id:
                    continue
                a, b = timestamp(flow['start_at']), timestamp(flow['end_at'])
                if b > start and a < end:
                    flows.append((a, b, number(flow['fixed_net_flow_bbl_per_hour'])))
            for a, b, rate in sorted(flows):
                left, right = max(start, a), min(end, b)
                if left != cursor or right <= left or left.microsecond or right.microsecond:
                    raise ValueError('Missing or overlapping process flow')
                if rate < 0:
                    withdrawn += -rate * Fraction(int((right-left).total_seconds()), 3600)
                cursor = right
            if cursor != end:
                raise ValueError('Incomplete process flow')
            minimum, maximum = number(tank['min_inventory_bbl']), number(tank['capacity_bbl'])
            if minimum < 0 or maximum < minimum:
                raise ValueError('Invalid tank limits')
            if quantity > maximum - minimum + withdrawn:
                removed += 1; continue
        except (KeyError, IndexError, TypeError, ValueError, OverflowError, ZeroDivisionError):
            pass
        retained.append((i, tank_id))
    return retained, removed
