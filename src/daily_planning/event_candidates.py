"""Versioned, evidence-backed search anchors; never operational commitments."""
from .context import local_time
from .berth_phases import occupancy_bounds

LEGACY = 'HEURISTIC_V1'
EVENTS = 'EVENTS_V1'


def event_anchors(call, fixed, previous):
    """Events supplied by the snapshot or derived by its existing simulator.

    FIX, pilotage, switching, weather release and tank events require verified
    source policies and are intentionally absent until those policies exist.
    """
    events = {local_time(call['available_at'])}
    events.update(local_time(occupancy_bounds(row)[1]) for row in fixed)
    if call['call_id'] in previous:
        events.add(local_time(previous[call['call_id']]['start_at']))
    return events
