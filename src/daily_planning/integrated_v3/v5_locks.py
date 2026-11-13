"""Persistent dynamic lock state; simulation locks are not operator approval."""
from copy import deepcopy
from datetime import timedelta
from ..integrated_input.validator import timestamp

MODES={'AUTO','MANUAL_LOCK','MANUAL_UNLOCK'}


def apply_overrides(state, changes, call_ids):
    result=deepcopy(state or {})
    for cid,mode in (changes or {}).items():
        if cid not in call_ids or mode not in MODES:raise ValueError('Invalid lock override: '+str(cid))
        result[cid]={'mode':mode}
    for cid in call_ids:result.setdefault(cid,{'mode':'AUTO'})
    return result


def resolve_locks(calls, *, as_of_at, previous_schedule=(), lock_state=None,
                  approved_commitments=(), completed_call_ids=(), allow_unscheduled_auto=False):
    """Return full-row hard commitments and pending missing-schedule decisions."""
    at=timestamp(as_of_at);by_id={c['call_id']:c for c in calls}
    rows={r['call_id']:r for r in previous_schedule};approved={r['call_id']:r for r in approved_commitments}
    if len(rows)!=len(previous_schedule) or len(approved)!=len(approved_commitments):raise ValueError('Duplicate schedule or approved commitment')
    if not set(approved)<=set(by_id) or not set(rows)<=set(by_id):raise ValueError('Unknown schedule call')
    done=set(completed_call_ids);state=apply_overrides(lock_state,{},by_id);locked=[];decisions={};missing=[]
    for cid,c in sorted(by_id.items()):
        mode=state[cid]['mode']
        if mode not in MODES:raise ValueError('Invalid lock mode: '+cid)
        if cid in done:
            decisions[cid]={'mode':mode,'status':'COMPLETED','origin':'PROJECTED_STATE'};continue
        prior=rows.get(cid);committed=approved.get(cid)
        if committed is not None and prior is not None and committed!=prior:raise ValueError('Approved commitment differs from prior schedule: '+cid)
        row=committed or prior
        started=row is not None and timestamp(row['berthing_start_at'])<=at
        if committed is not None:reason='OPERATOR_APPROVED';origin='OPERATOR_APPROVED_COMMITMENT'
        elif started:reason='STARTED';origin='SIMULATED_STARTED_PROJECTION'
        elif mode=='MANUAL_LOCK':reason='MANUAL_LOCK';origin='SIMULATED_MANUAL_LOCK'
        elif mode=='MANUAL_UNLOCK':reason=None;origin='MANUAL_UNLOCK'
        elif timestamp(c['eta_at'])<=at+timedelta(hours=5):reason='ETA_WITHIN_5_HOURS';origin='SIMULATED_AUTOMATIC_LOCK'
        else:reason=None;origin='AUTO_CHANGEABLE'
        if (allow_unscheduled_auto and reason=='ETA_WITHIN_5_HOURS' and row is None
                and committed is None and mode=='AUTO'):
            reason=None;origin='UNSCHEDULED_NOT_PRIOR_COMMITMENT'
        if reason:
            if row is None:missing.append({'code':'LOCK_REQUIRES_EXISTING_SCHEDULE','call_id':cid,'reason':reason});decisions[cid]={'mode':mode,'status':'LOCK_PENDING','origin':origin};continue
            locked.append(deepcopy(row));decisions[cid]={'mode':mode,'status':'LOCKED','reason':reason,'origin':origin,'hard_fix_etb_preserved':c.get('fixed_etb_is_hard') is True}
        else:decisions[cid]={'mode':mode,'status':'CHANGEABLE','origin':origin,'hard_fix_etb_preserved':c.get('fixed_etb_is_hard') is True}
    return {'lock_state':state,'decisions':decisions,'physical_commitments':locked,'issues':missing,'operational_approval':False}
