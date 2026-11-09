"""Fail-closed forecast input validation without database writes."""
from ..context import signature
from ..bootstrap_db import kst_time


def forecast_time(value):
    """Convert timestamps to naive KST without rounding physical events."""
    return kst_time(value).replace(tzinfo=None)


def validate_snapshot(snapshot):
    if signature({k: v for k, v in snapshot.items() if k != 'snapshot_hash'}) != snapshot.get('snapshot_hash'):
        raise ValueError('Frozen input snapshot hash mismatch')
    as_of = forecast_time(snapshot['as_of_at'])
    seen = set()
    for call in snapshot['calls']:
        if not call.get('call_id') or call['call_id'] in seen:
            raise ValueError('Missing or duplicate call identifier')
        seen.add(call['call_id'])
        if not call.get('known_at'):
            raise ValueError('Call information availability is missing')
        forecast_time(call['known_at'])
    for commit in snapshot.get('commits', []):
        if not commit.get('confirmed_at'):
            raise ValueError('Commit confirmation time is missing')
        if forecast_time(commit['confirmed_at']) > as_of:
            raise ValueError('Commit confirmation was not known at planning time')
    return as_of
