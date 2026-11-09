"""Read-only confirmed and provenance-verified tentative occupancy."""
from ..context import local_time
from ..bootstrap_db import kst_time


def project_berths(snapshot, known_ids, as_of, forecast_end):
    rows, missing = [], []
    berth_ids = {berth['berth_id'] for berth in snapshot.get('berths', [])}
    for kind, records in (('CONFIRMED', snapshot.get('commits', [])),
                          ('TENTATIVE', snapshot.get('previous_tentative_assignments', []))):
        for row in records:
            if row.get('call_id') not in known_ids:
                continue
            if kind == 'TENTATIVE':
                known_at = row.get('known_at')
                if not known_at:
                    missing.append({'call_id': row['call_id'], 'field': 'tentative_known_at'})
                    continue
                if local_time(known_at) > as_of:
                    continue
                start, end = row.get('start_at'), row.get('completion_at')
            else:
                known_at = row['confirmed_at']
                start, end = row.get('locked_from_at'), row.get('locked_until_at')
            if not row.get('berth_id') or not start or not end:
                missing.append({'call_id': row['call_id'], 'field': 'occupancy_interval'})
                continue
            if row['berth_id'] not in berth_ids:
                missing.append({'call_id': row['call_id'], 'field': 'unknown_berth'})
                continue
            a, b = local_time(start), local_time(end)
            if a >= b:
                raise ValueError('Invalid occupancy interval')
            if a < forecast_end and b > as_of:
                rows.append({'call_id': row['call_id'], 'berth_id': row['berth_id'],
                             'start_at': start, 'end_at': end, 'known_at': known_at,
                             'classification': kind,
                             'source': 'SNAPSHOT_COMMIT' if kind == 'CONFIRMED' else 'PREVIOUS_SELECTED_PLAN'})
    rows.sort(key=lambda r: (r['berth_id'], local_time(r['start_at']), r['call_id']))
    conflicts = []
    for i, left in enumerate(rows):
        for right in rows[i + 1:]:
            if left['berth_id'] == right['berth_id'] and max(local_time(left['start_at']), local_time(right['start_at'])) < min(local_time(left['end_at']), local_time(right['end_at'])):
                conflicts.append({'berth_id': left['berth_id'], 'call_ids': sorted([left['call_id'], right['call_id']]),
                                  'certainty': 'CONFIRMED' if left['classification'] == right['classification'] == 'CONFIRMED' else 'POTENTIAL'})
    return rows, conflicts, missing


def congestion_periods(snapshot, rows, as_of, forecast_end):
    """Only flag full active-berth occupation supported by stored intervals."""
    active = {berth['berth_id'] for berth in snapshot.get('berths', []) if berth.get('is_active')}
    if not active:
        return []
    edges = {as_of, forecast_end}
    for row in rows:
        if row['berth_id'] in active:
            edges.add(max(as_of, local_time(row['start_at'])))
            edges.add(min(forecast_end, local_time(row['end_at'])))
    ordered = sorted(edges)
    periods = []
    for a, b in zip(ordered, ordered[1:]):
        occupied = [r for r in rows if r['berth_id'] in active and local_time(r['start_at']) < b and local_time(r['end_at']) > a]
        if {r['berth_id'] for r in occupied} != active:
            continue
        confirmed = {r['berth_id'] for r in occupied if r['classification'] == 'CONFIRMED'} == active
        certainty = 'CONFIRMED' if confirmed else 'POTENTIAL'
        if periods and periods[-1]['end_at'] == kst_time(a).isoformat() and periods[-1]['certainty'] == certainty:
            periods[-1]['end_at'] = kst_time(b).isoformat()
        else:
            periods.append({'start_at': kst_time(a).isoformat(), 'end_at': kst_time(b).isoformat(), 'certainty': certainty,
                            'occupied_active_berths': len(active), 'active_berths': len(active)})
    return periods
