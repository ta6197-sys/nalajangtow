"""Evidence-backed finite ETB anchors; none is an operational commitment."""
from datetime import timedelta
from ..integrated_input.validator import timestamp
from ..integrated_input.provenance import canonical, digest
from ..physical_scheduling.compatibility import compatibility
from ..physical_scheduling.policy import validate_policy

VERSION='V3_ETB_EVENTS_V1'


def generate_candidate_etbs(snapshot, *, as_of_at, policy, commitments=(), previous_schedule=(), completed_rows=()):
    """Return source-labelled ETB anchors inside the 72-hour start horizon.

    Availability is POB availability; physical constraints are rechecked by the
    independent replay. Previous timestamps are optional hints, never locks.
    """
    begin=timestamp(as_of_at);end=begin+timedelta(hours=72)
    berths=[b for b in snapshot['berths'] if b.get('active')=='Y']
    validate_policy(policy,[b['berth_id'] for b in berths])
    calls={c['call_id']:c for c in snapshot['calls']}
    if len(calls)!=len(snapshot['calls']):raise ValueError('Duplicate call ID')
    previous={}
    for row in previous_schedule:
        cid=row['call_id']
        if cid not in calls or cid in previous:raise ValueError('Unknown or duplicate previous call')
        previous[cid]=timestamp(row['etb_at'])
    locked=list(commitments);locked_ids={r['call_id'] for r in locked}
    if len(locked_ids)!=len(locked) or not locked_ids<=set(calls):raise ValueError('Unknown or duplicate commitment')
    # Only trusted physical validator may determine if a commitment is usable.
    from ..physical_scheduling.validator import validate_result
    check=validate_result(snapshot,locked,policy)
    if check['status']!='VALID':raise ValueError('Commitment validation failed: '+str(check['issues']))
    from ..physical_scheduling.completed import completed_call_ids
    completed=completed_call_ids(snapshot,completed_rows,policy,as_of_at=as_of_at,locked_ids=locked_ids)
    result={};audit={};outside=[]
    for cid,call in sorted(calls.items()):
        if cid in locked_ids or cid in completed:continue
        eligible=timestamp(call['fix_etb_at']) if call['fixed_etb_is_hard'] else timestamp(call['eta_at'])
        if eligible>=end:outside.append(cid);continue
        anchors={}
        def add(at,reason):
            if begin<=at<end:anchors.setdefault(at,set()).add(reason)
        if call['fixed_etb_is_hard']:
            add(eligible,'HARD_FIX_ETB')
        else:
            if cid in previous:add(previous[cid],'PREVIOUS_TENTATIVE_ETB')
            for berth in berths:
                if compatibility(call,berth):continue
                bid=berth['berth_id'];config=policy['berths'][bid]
                maneuver=timedelta(seconds=config['berthing_seconds']+config.get('pilot_boarding_to_berthing_seconds',0))
                add(max(begin,timestamp(call['eta_at']))+maneuver,'AVAILABILITY:'+bid)
                for row in locked:
                    if row['berth_id']==bid:
                        add(timestamp(row['physical_clearance_at'])+timedelta(seconds=config['berthing_seconds']),'COMMITTED_CLEARANCE:'+bid)
                        add(timestamp(row['switching_end_at']),'COMMITTED_SWITCH_RELEASE:'+bid)
                windows=(config.get('pilotage_windows_by_call') or {}).get(cid)
                windows=windows.get('berthing') if windows is not None else config.get('pilotage_windows')
                for window in windows or []:
                    add(max(begin,timestamp(call['eta_at']),timestamp(window['start_at']))+maneuver,'PILOTAGE_WINDOW:'+bid)
        result[cid]=[at.isoformat() for at in sorted(anchors)]
        audit[cid]=[{'etb_at':at.isoformat(),'sources':sorted(anchors[at])} for at in sorted(anchors)]
    return {'model_version':VERSION,'candidate_etbs':result,'anchors':audit,'outside_horizon_call_ids':outside,'completed_call_ids':sorted(completed),
            'input_fingerprint':digest(canonical({'snapshot':snapshot,'as_of_at':as_of_at,'policy':policy,'commitments':locked,'previous_schedule':previous_schedule,**({'completed_rows':list(completed_rows)} if completed_rows else {})}).encode()),
            'scope':'Finite evidence-backed anchors only; not exhaustive or automatically feasible', 'operational_approval':False}
