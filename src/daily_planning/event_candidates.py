"""Versioned, evidence-backed search anchors; never operational commitments."""
from .context import local_time
from .berth_phases import occupancy_bounds

LEGACY = 'HEURISTIC_V1'
EVENTS = 'EVENTS_V1'
EVENTS_WEATHER = 'EVENTS_WEATHER_V2'


def event_anchors(call, fixed, previous):
    """Events supplied by the snapshot or derived by its existing simulator.

    FIX, pilotage, switching and tank events require verified
    source policies and are intentionally absent until those policies exist.
    """
    events = {local_time(call['available_at'])}
    events.update(local_time(occupancy_bounds(row)[1]) for row in fixed)
    if call['call_id'] in previous:
        events.add(local_time(previous[call['call_id']]['start_at']))
    return events


def weather_release_events(weather, rule, begin, end):
    """Find blocked-to-workable transitions using the existing weather rule.

    These are optional search anchors, not physical event grid constraints.
    Missing weather remains an error; no releases are fabricated.
    """
    if begin >= end:
        return set()
    at = begin
    blocked = weather.blocked(at, rule)
    releases = set()
    while at < end:
        _, next_at, _ = weather.slot(at, rule)
        if next_at >= end:
            break
        next_blocked = weather.blocked(next_at, rule)
        if blocked and not next_blocked:
            releases.add(next_at)
        blocked, at = next_blocked, next_at
    return releases
