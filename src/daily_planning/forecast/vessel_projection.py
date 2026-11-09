"""Demand known at the snapshot time; never invent assignments or estimates."""
from ..context import local_time


def project_vessels(snapshot, as_of, forecast_end):
    known, missing = [], []
    for call in sorted(snapshot['calls'], key=lambda row: row['call_id']):
        if local_time(call['known_at']) > as_of:
            continue
        if not call.get('available_at'):
            missing.append({'call_id': call['call_id'], 'field': 'available_at'})
            continue
        available = local_time(call['available_at'])
        if available >= forecast_end:
            continue
        known.append({'call_id': call['call_id'], 'available_at': call['available_at'],
                      'known_at': call['known_at'], 'source': 'SNAPSHOT_CALL',
                      'classification': 'KNOWN_CALL'})
    return known, missing


def project_uncertain(snapshot, as_of, forecast_end):
    """Return supplied, timestamped predictions without converting them to jobs."""
    output, missing = [], []
    for item in snapshot.get('uncertain_forecasts', []):
        if not item.get('known_at'):
            missing.append({'field': 'uncertain_forecast_provenance'})
            continue
        if local_time(item['known_at']) > as_of:
            continue
        if not item.get('source') or not item.get('forecast_id'):
            missing.append({'field': 'uncertain_forecast_provenance'})
            continue
        if not item.get('predicted_at'):
            missing.append({'forecast_id': item['forecast_id'], 'field': 'predicted_at'})
            continue
        predicted_at = local_time(item['predicted_at'])
        if as_of <= predicted_at < forecast_end:
            output.append({'forecast_id': item['forecast_id'], 'known_at': item['known_at'],
                           'predicted_at': item['predicted_at'], 'source': item['source'],
                           'classification': 'UNCERTAIN_FORECAST'})
    output.sort(key=lambda item: (local_time(item['predicted_at']), item['forecast_id']))
    return output, missing
