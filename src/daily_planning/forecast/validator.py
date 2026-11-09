"""Fail-closed forecast input validation without database writes."""
from ..context import local_time, signature


def validate_snapshot(snapshot):
    if signature({k: v for k, v in snapshot.items() if k != 'snapshot_hash'}) != snapshot.get('snapshot_hash'):
        raise ValueError('Frozen input snapshot hash mismatch')
    as_of = local_time(snapshot['as_of_at'])
    seen = set()
    for call in snapshot['calls']:
        if not call.get('call_id') or call['call_id'] in seen:
            raise ValueError('Missing or duplicate call identifier')
        seen.add(call['call_id'])
        if not call.get('known_at'):
            raise ValueError('Call information availability is missing')
        local_time(call['known_at'])
    for commit in snapshot.get('commits', []):
        if not commit.get('confirmed_at'):
            raise ValueError('Commit confirmation time is missing')
        if local_time(commit['confirmed_at']) > as_of:
            raise ValueError('Commit confirmation was not known at planning time')
    return as_of
