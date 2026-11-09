"""Opt-in 168-hour, provenance-aware decision support."""
from datetime import timedelta

from ..horizon_policy import LOOKAHEAD_168H_V1
from ..context import signature
from .validator import validate_snapshot, forecast_time
from .vessel_projection import project_vessels, project_uncertain
from .berth_projection import project_berths, congestion_periods


def generate_forecast(snapshot, *, policy=LOOKAHEAD_168H_V1, selection_provenance=None):
    if policy != LOOKAHEAD_168H_V1:
        raise ValueError('Forecast requires LOOKAHEAD_168H_V1')
    as_of = validate_snapshot(snapshot)
    end = as_of + timedelta(hours=policy.forecast_hours)
    vessels, missing = project_vessels(snapshot, as_of, end)
    uncertain, uncertain_missing = project_uncertain(snapshot, as_of, end)
    known_ids = {v['call_id'] for v in vessels}
    occupancy, conflicts, berth_missing = project_berths(snapshot, known_ids, as_of, end, selection_provenance)
    assigned = {r['call_id'] for r in occupancy}
    master_missing = []
    for field in ('contract', 'berth'):
        metadata = snapshot.get(field + '_master_provenance', {})
        if not (metadata.get('source') and metadata.get('known_at') and forecast_time(metadata['known_at']) <= as_of):
            master_missing.append({'field': field + '_master_history', 'reason': 'AS_OF_UNVERIFIED'})
    return {'schema_version': 'FORECAST_V1', 'policy_version': policy.version,
            'snapshot_hash': snapshot['snapshot_hash'], 'selection_provenance_hash': signature(selection_provenance or {}), 'boundaries': policy.boundaries(snapshot['as_of_at']),
            'vessels': vessels, 'uncertain_forecasts': uncertain, 'occupancy': occupancy, 'conflicts': conflicts,
            'congestion_periods': congestion_periods(snapshot, occupancy, as_of, end),
            'unassigned_call_ids': sorted(known_ids - assigned),
            'missing_data': missing + berth_missing + uncertain_missing + master_missing + [{'field': 'fixed_vessel_policy', 'reason': 'NOT_SUPPLIED'}] + ([] if 'uncertain_forecasts' in snapshot else [{'field': 'uncertain_forecasts', 'reason': 'NOT_SUPPLIED'}]),
            'read_only': True}
