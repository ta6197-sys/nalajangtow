"""Proof-limited pilotage-gap classification for an opt-in V5 Rolling policy.

A certificate proves there is NO ETB in [as_of, as_of+72h) satisfying
arrival time AND all of a call's known inbound pilotage windows, for ANY
compatible berth. It makes no claim about work, tank, settlement, or subsequent
windows; a possible pilotage slot is never a feasible schedule certificate.

The classifier deliberately ignores other restrictions (including 02:00–08:00).
Ignoring restrictions only weakens the no-slot proof; it cannot turn a possible
ETB into a certified no-slot result. Old checkpoints never call this module.
"""
from __future__ import annotations
from datetime import datetime, timedelta

PILOTAGE_GAP_VERSION = 'V5_HORIZON_PILOTAGE_GAP_V1'
HORIZON_HOURS = 72


def _dt(value):
    x = datetime.fromisoformat(value) if isinstance(value,str) else value
    if not isinstance(x,datetime) or x.tzinfo is None or x.utcoffset() is None:
        raise ValueError('Timezone-aware ISO datetime required')
    return x


def _secs(value):
    if type(value) is not int or value < 0 or value > 24*3600:
        raise ValueError('Verified nonnegative maneuver seconds required')
    return value


def certify_no_pilotage_slot(call, eligible_berths, prepared_policy, as_of_at):
    """Return a no-current-horizon-slot certificate, or None (NOT proved).

    The per-call windows MUST be derived by the existing verified user V5
    pilotage module. This function never infers windows from policy defaults.
    """
    at = _dt(as_of_at)
    horizon = at + timedelta(hours=HORIZON_HOURS)
    if not isinstance(call,dict) or call.get('fixed_etb_is_hard') is not False:
        return None
    cid=call.get('call_id')
    if not isinstance(cid,str) or not cid or not isinstance(eligible_berths,(list,tuple)) or not eligible_berths:
        return None
    if len(set(eligible_berths)) != len(eligible_berths):
        return None
    eta=_dt(call['eta_at'])
    # The ordinary due scope is eta < end; do not invent a new deferral for
    # future calls which the existing rolling engine already leaves pending.
    if eta >= horizon:
        return None
    if not isinstance(prepared_policy,dict) or not isinstance(prepared_policy.get('berths'),dict):
        return None
    evidence={}
    for bid in sorted(eligible_berths):
        cfg=prepared_policy['berths'].get(bid)
        if not isinstance(cfg,dict):return None
        by_call=cfg.get('pilotage_windows_by_call')
        pair=(by_call or {}).get(cid)
        if not isinstance(pair,dict) or not isinstance(pair.get('berthing'),list) or not pair['berthing']:
            return None  # unknown policy is NOT a deferral certificate
        maneuver=timedelta(seconds=_secs(cfg['berthing_seconds'])+_secs(cfg.get('pilot_boarding_to_berthing_seconds',0)))
        earliest=max(at,eta)
        next_lower_bound=None
        windows_checked=0
        for window in pair['berthing']:
            if not isinstance(window,dict):return None
            begin=_dt(window['start_at']);end=_dt(window['end_at'])
            if begin>=end:return None
            latest=_dt(window['latest_start_at']) if window.get('latest_start_at') else end
            pob=max(begin,earliest)
            etb=pob+maneuver
            # Existing phases.covered() requires the *entire* maneuver within
            # one window, and POB <= optional latest_start_at.
            if pob<=latest and etb<=end:
                if etb < horizon:
                    return None  # even one possible pilotage slot disproves gap
                if next_lower_bound is None or etb<next_lower_bound:
                    next_lower_bound=etb
            windows_checked+=1
        evidence[bid]={'windows_checked':windows_checked,
                       'next_pilotage_only_etb_lower_bound':next_lower_bound.isoformat() if next_lower_bound else None}
    return {'certificate_version':PILOTAGE_GAP_VERSION,'call_id':cid,
            'proof_scope':'NO_ARRIVAL_AND_INBOUND_PILOTAGE_ETB_IN_CURRENT_72H_WINDOW',
            'as_of_at':at.isoformat(),'horizon_end_exclusive':horizon.isoformat(),
            'eta_at':eta.isoformat(),'eligible_berths':sorted(eligible_berths),
            'berth_evidence':evidence,
            'next_window_feasibility':'NOT_PROVEN',
            'physical_global_infeasibility':'NOT_CLAIMED'}


def collect_certificates(snapshot, candidate_ids, *, as_of_at, policy, pilotage_context,
                         protected_ids=(), previously_scheduled_ids=()):
    """Read-only adapter to repo's verified eligibility and pilotage derivation.

    Any unresolved call is left to the original solver/failure path; a missing
    berth, incomplete context or unsupported pilotage is never auto-deferred.
    """
    from ..integrated_input.v5 import eligible_berth_ids
    from .v5_pilotage import prepare_pilotage_policy
    from ..physical_scheduling.v5_user_rules import enabled as user_policy_enabled
    if not user_policy_enabled(policy):
        raise ValueError('V5 pilotage gap requires the explicit user policy')
    ids=list(candidate_ids)
    calls={c['call_id']:c for c in snapshot['calls']}
    if len(ids)!=len(set(ids)) or not set(ids)<=set(calls):
        raise ValueError('Invalid candidate identifiers')
    protected=set(protected_ids)|set(previously_scheduled_ids)
    out={};unresolved={}
    for cid in sorted(ids):
        c=calls[cid]
        if cid in protected or c.get('fixed_etb_is_hard') is not False:
            continue
        if _dt(c['eta_at']) >= _dt(as_of_at)+timedelta(hours=HORIZON_HOURS):
            continue
        eligible=eligible_berth_ids(c,snapshot['berths'],snapshot['tanks'])
        if not eligible:
            unresolved[cid]='NO_COMPATIBLE_BERTH_NOT_AUTO_DEFERRED';continue
        prepared,issue=prepare_pilotage_policy(snapshot,[cid],{cid:eligible},policy,pilotage_context,as_of_at,{})
        if issue or prepared is None:
            unresolved[cid]='PILOTAGE_POLICY_UNRESOLVED_NOT_AUTO_DEFERRED';continue
        try:
            cert=certify_no_pilotage_slot(c,eligible,prepared,as_of_at)
        except (KeyError,ValueError,TypeError,OverflowError):
            unresolved[cid]='PILOTAGE_PROOF_INVALID_NOT_AUTO_DEFERRED';continue
        if cert is not None:out[cid]=cert
    return {'certificates':out,'unresolved':unresolved}
