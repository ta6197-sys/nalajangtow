"""Opt-in 168-hour, provenance-aware decision support."""
from datetime import timedelta

from ..horizon_policy import LOOKAHEAD_168H_V1
from .validator import validate_snapshot
from .vessel_projection import project_vessels, project_uncertain
from .berth_projection import project_berths, congestion_periods


def generate_forecast(snapshot, *, policy=LOOKAHEAD_168H_V1):
    if policy != LOOKAHEAD_168H_V1:
        raise ValueError('Forecast requires LOOKAHEAD_168H_V1')
    as_of = validate_snapshot(snapshot)
    end = as_of + timedelta(hours=policy.forecast_hours)
    vessels, missing = project_vessels(snapshot, as_of, end)
    uncertain, uncertain_missing = project_uncertain(snapshot, as_of, end)
    known_ids = {v['call_id'] for v in vessels}
    occupancy, conflicts, berth_missing = project_berths(snapshot, known_ids, as_of, end)
    assigned = {r['call_id'] for r in occupancy}
    return {'schema_version': 'FORECAST_V1', 'policy_version': policy.version,
            'snapshot_hash': snapshot['snapshot_hash'], 'boundaries': policy.boundaries(snapshot['as_of_at']),
            'vessels': vessels, 'uncertain_forecasts': uncertain, 'occupancy': occupancy, 'conflicts': conflicts,
            'congestion_periods': congestion_periods(snapshot, occupancy, as_of, end),
            'unassigned_call_ids': sorted(known_ids - assigned),
            'missing_data': missing + berth_missing + uncertain_missing + [{'field': 'fixed_vessel_policy', 'reason': 'NOT_SUPPLIED'}] + ([] if 'uncertain_forecasts' in snapshot else [{'field': 'uncertain_forecasts', 'reason': 'NOT_SUPPLIED'}]),
            'read_only': True}
