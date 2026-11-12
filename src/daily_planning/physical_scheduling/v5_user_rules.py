"""V5 user-confirmed berth-policy primitives (2026-11-12).

This module is intentionally independent of CP-SAT. The candidate-pair
constraints, independent validator and rollout diagnostics must share the same
policy semantics, but solver optimization proof remains a separate assertion.
All berth IDs denote independent virtual berths, even when reference names repeat.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from fractions import Fraction
from copy import deepcopy
import csv
from pathlib import Path

VERSION = 'V5_USER_ETU_SWITCHING_20261112_V1'
DOCK_VERSION = 'USER_GRT3000_FIXED120_V1'
DOCK_PAIR = frozenset(('BERTH-07', 'BERTH-08'))
HOSE_DISCONNECT_SECONDS = 7200
# reference facility; direction seconds; switching seconds
BERTH_RULES = {
    'BERTH-01': ('#6', 5400, 9000),
    'BERTH-02': ('#7', 5400, 9000),
    'BERTH-03': ('#8', 5400, 9000),
    'BERTH-04': ('#1', 3600, 5400),
    'BERTH-05': ('#1', 3600, 5400),
    'BERTH-06': ('#2', 3600, 5400),
    'BERTH-07': ('#4-1', 3600, 3600),
    'BERTH-08': ('#4-2', 3600, 3600),
    'BERTH-09': ('#3', 3600, 5400),
    'BERTH-10': ('#5-5', 3600, 3600),
    'BERTH-11': ('#8', 5400, 9000),
}


def enabled(policy):
    return isinstance(policy, dict) and policy.get('schema_version') == VERSION


def moment(text):
    value = datetime.fromisoformat(text) if isinstance(text, str) else text
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timezone-aware timestamp required')
    return value


def exact(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Exact numeric field required')
    result = Fraction(str(value))
    if result.denominator == 0:
        raise ValueError('Invalid rational')
    return result


def verify_v5_policy(policy, berth_ids):
    if not enabled(policy):
        raise ValueError('Explicit V5 user policy version required')
    if policy.get('data_origin') != 'SYNTHETIC_FIXTURE' or policy.get('operational_approval') is not False:
        raise ValueError('V5 policy is simulation only')
    if not set(berth_ids) <= set(BERTH_RULES) or set(policy.get('berths', {})) != set(BERTH_RULES):
        raise ValueError('V5 policy must explicitly cover all 11 independent berths')
    if type(policy.get('hose_disconnect_seconds')) is not int or policy['hose_disconnect_seconds'] != HOSE_DISCONNECT_SECONDS:
        raise ValueError('Explicit 120-minute hose disconnect is required')
    if policy.get('dock_4142_version') != DOCK_VERSION or set(policy.get('dock_4142_berths', ())) != DOCK_PAIR:
        raise ValueError('Explicit BERTH-07/08 dock rule required')
    for bid, (_, direction, switching) in BERTH_RULES.items():
        cfg = policy.get('berths', {}).get(bid)
        if not isinstance(cfg, dict):
            raise ValueError('Missing V5 berth policy: ' + bid)
        if any(type(cfg.get(field)) is not int or cfg[field] != value for field, value in (
                ('berthing_seconds', direction), ('direction_seconds', direction),
                ('switching_seconds', switching), ('unberthing_seconds', 0))):
            raise ValueError('Incorrect direction/switching/zero-ETU configuration: ' + bid)
        if cfg.get('pilot_boarding_to_berthing_seconds', 0) != 0 or cfg.get('pilot_boarding_to_unberthing_seconds', 0) != 0:
            raise ValueError('Unverified transit duration: ' + bid)
    return True


def no_night_etb(call, etb):
    """Only the resolved GRT 30k+ 02:00–08:00 branch; ambiguous groups fail closed upstream."""
    g = exact((call.get('physical') or {})['grt'])
    t = moment(etb)
    return 30000 < g < 75000 and 2 <= t.hour < 8


def _span(a, b, c, d):
    return max(moment(a), moment(c)) < min(moment(b), moment(d))


def _grt(call):
    return exact((call.get('physical') or {})['grt'])


def _sort_pair(a, b):
    return sorted((a, b), key=lambda r: (moment(r['etb_at']), r['call_id']))


def pair_errors(a, b, snapshot, policy):
    """Independent exact user-policy pairwise constraints.

    Identical reference *names* must not create coupling. Two dock berths
    have a user-provided, documented exception; no other cross-berth rule
    is inferred. Explicit source groups are checked by the caller.
    """
    if a['call_id'] == b['call_id']:
        return ['DUPLICATE_CALL']
    aid, bid = a['berth_id'], b['berth_id']
    if aid not in BERTH_RULES or bid not in BERTH_RULES:
        return ['UNKNOWN_BERTH']
    if aid == bid:
        first, second = _sort_pair(a, b)
        if _span(first['berthing_start_at'], first['etu_at'], second['berthing_start_at'], second['etu_at']):
            return ['SAME_BERTH_OCCUPANCY']
        release = moment(first['etu_at']) + timedelta(seconds=BERTH_RULES[aid][2])
        return ['SAME_BERTH_SWITCHING'] if moment(second['etb_at']) < release else []
    if frozenset((aid, bid)) != DOCK_PAIR:
        return []
    calls = {c['call_id']: c for c in snapshot['calls']}
    try:
        small = _grt(calls[a['call_id']]) <= 3000 and _grt(calls[b['call_id']]) <= 3000
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return ['DOCK_GRT_UNRESOLVED']
    first, second = _sort_pair(a, b)
    if _span(first['etb_at'], first['etu_at'], second['etb_at'], second['etu_at']):
        if not small:
            return ['DOCK_NONSMALL_OVERLAP']
        issues = []
        if moment(second['etb_at']) != moment(first['work_end_at']):
            issues.append('DOCK_EXACT_120_MIN_OVERLAP_REQUIRED')
        if moment(second['hose_connection_start_at']) < moment(first['etu_at']):
            issues.append('DOCK_SHARED_HOSE_NOT_RELEASED')
        if any(_span(i[0], i[1], j[0], j[1]) for i in first['work_intervals'] for j in second['work_intervals']):
            issues.append('DOCK_CONCURRENT_CARGO')
        if _span(first['work_end_at'], first['hose_disconnect_end_at'],
                 second['hose_connection_start_at'], second['hose_connection_end_at']):
            issues.append('DOCK_HOSE_CONNECT_DISCONNECT_OVERLAP')
        return issues
    # Nonoverlap: normal switching between the two dock positions.
    release = moment(first['etu_at']) + timedelta(seconds=BERTH_RULES[first['berth_id']][2])
    return ['DOCK_SEQUENTIAL_SWITCHING'] if moment(second['etb_at']) < release else []


def eligible_4x_overlap(first, next_call):
    try:
        return _grt(next_call) <= 3000 and first['berth_id'] in DOCK_PAIR
    except (KeyError, ValueError, TypeError):
        return False


def prepare_user_pilotage(snapshot, call_ids, eligible, policy, context, as_of_at, history_start_at=None):
    """Versioned arrival pilotage only. Outbound is *not* a constraint on ETU.

    Because the screenshot does not resolve 75k+ and low-GRT deep-draft
    import Boolean logic, those calls are explicitly blocked, not guessed.
    The user-specified 02:00–08:00 ban is also enforced on each ETB option.
    """
    def pending(reason, **kw):
        return None, dict(code='UNSUPPORTED_OPERATIONAL_POLICY', reason=reason, **kw)
    if not isinstance(context, dict) or not context.get('daylight_path') or not context.get('daylight_source'):
        return pending('Versioned daylight source is required')
    if not isinstance(context.get('night_berthing_permissions'), dict):
        return pending('Explicit night permissions per berth required')
    try:
        from ..integrated_input.provenance import digest
        from ..physical_scheduling.pilotage import derive_windows
        raw = Path(context['daylight_path']).read_bytes()
        days = list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))
        if not days:
            return pending('Empty daylight source')
        start = moment(as_of_at)
        end = start + timedelta(hours=72)
        earliest = min(start, moment(history_start_at)) if history_start_at else start
        needed = {(earliest+timedelta(days=i)).date().isoformat() for i in range((end.date()-earliest.date()).days+1)}
        entries = [d for d in days if d.get('date_kst') in needed]
        if set(d['date_kst'] for d in entries) != needed:
            return pending('Daylight dates missing', missing_dates=sorted(needed - {d['date_kst'] for d in entries}))
        result = deepcopy(policy)
        calls = {c['call_id']: c for c in snapshot['calls']}
        for cid in call_ids:
            call = calls[cid]
            grt = _grt(call)
            draft = exact(call['physical']['draft_m'])
            operation = str(call.get('direction', '')).strip().upper()
            # Exact threshold interpretation and special combinations are unresolved.
            if grt in (30000, 50000) or grt >= 75000:
                return pending('Unresolved exact GRT boundary/75k rule', call_id=cid, grt=str(grt))
            if grt < 30000 and draft >= Fraction('10.5') and operation not in ('EXPORT','OUT','OUTBOUND','LOAD','LOADING'):
                return pending('Low-GRT deep-draft import condition unresolved', call_id=cid, draft=str(draft), operation=operation)
            offset = 0 if grt < 30000 else (5400 if grt < 50000 else 7200)
            for bid in eligible[cid]:
                permission = context['night_berthing_permissions'].get(bid)
                if type(permission) is not bool:
                    return pending('Missing berth night permission', call_id=cid, berth_id=bid)
                windows = derive_windows(call, entries, night_permitted=permission,
                                         last_pob_offset_seconds=offset, last_pob_basis='START')
                result['berths'][bid].setdefault('pilotage_windows_by_call', {})[cid] = {'berthing': windows}
        result['v5_pilotage_provenance'] = {
            'daylight_sha256': digest(raw), 'daylight_source': context['daylight_source'],
            'inbound': 'GRT_DIRECTION_USER_20261112_V1', 'outbound': 'ETU_24H_ALLOWED',
            'unresolved_boundary_cases': 'FAIL_CLOSED'}
        return result, None
    except (KeyError, ValueError, TypeError, OSError, ZeroDivisionError) as exc:
        return pending(str(exc))
