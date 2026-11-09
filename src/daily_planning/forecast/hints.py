"""Optional independently replayed hints, never operational commitments."""
from pathlib import Path

from weather_optimizer import WeatherSeries
from ..context import local_time, signature
from ..evaluation import evaluate_baseline
from ..input_loader import fingerprint
from ..horizon_policy import LOOKAHEAD_168H_V1


def validated_planning_hint(snapshot, forecast, weather_path):
    """Validate known tentative starts within 72h; do not modify snapshot or records."""
    if forecast.get('snapshot_hash') != snapshot.get('snapshot_hash'):
        raise ValueError('Forecast belongs to a different snapshot')
    if signature({k: v for k, v in snapshot.items() if k != 'snapshot_hash'}) != snapshot.get('snapshot_hash'):
        raise ValueError('Frozen input snapshot hash mismatch')
    if (forecast.get('policy_version') != LOOKAHEAD_168H_V1.version or
            forecast.get('boundaries') != LOOKAHEAD_168H_V1.boundaries(snapshot['as_of_at'])):
        raise ValueError('Forecast policy or boundaries differ')
    actual = fingerprint(Path(weather_path))
    expected = snapshot['weather']
    if any(actual.get(key) != expected.get(key) for key in ('sha256', 'bytes')):
        raise ValueError('Weather file differs from frozen snapshot')
    begin = local_time(snapshot['as_of_at'])
    end = local_time(forecast['boundaries']['optimization_end_at'])
    rows = [{'call_id': r['call_id'], 'berth_id': r['berth_id'], 'start_at': r['start_at']}
            for r in forecast['occupancy'] if r['classification'] == 'TENTATIVE'
            and begin <= local_time(r['start_at']) < end]
    original = {(r['call_id'], r['berth_id'], local_time(r['start_at']))
                for r in snapshot.get('previous_tentative_assignments', [])
                if r.get('call_id') and r.get('berth_id') and r.get('start_at')}
    if any((r['call_id'], r['berth_id'], local_time(r['start_at'])) not in original for r in rows):
        raise ValueError('Hint differs from previous tentative schedule')
    if not rows:
        return {'status': 'NO_VALIDATED_HINT', 'reason': 'NO_KNOWN_TENTATIVE_ASSIGNMENTS', 'assignments': []}
    result = evaluate_baseline(snapshot, rows, WeatherSeries.from_csv(Path(weather_path)))
    return {'status': 'VALIDATED', 'assignments': rows, 'schedule_signature': result['schedule_signature'],
            'demurrage_cents': result['evaluation']['summary']['demurrage_increment_to_completion_cents'],
            'scope': 'Existing independent BASELINE replay; not a confirmed schedule'}
