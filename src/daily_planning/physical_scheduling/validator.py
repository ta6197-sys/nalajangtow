"""Independent replay and phase-resource validation, separate from CP-SAT."""
from datetime import timedelta
from ..integrated_input.validator import timestamp,tokens
from .phases import replay


def overlap(a,b,c,d):return max(timestamp(a),timestamp(c))<min(timestamp(b),timestamp(d))



def resource_intervals(row,phases):
    mapping={'BERTHING':[[row['berthing_start_at'],row['etb_at']]],'HOSE_CONNECT':[[row['hose_connection_start_at'],row['hose_connection_end_at']]],
             'CARGO':row['work_intervals'],'HOSE_DISCONNECT':[[row['work_end_at'],row['hose_disconnect_end_at']]],
             'SWITCHING':[[row['switching_start_at'],row['switching_end_at']]],'UNBERTHING':[[row['unberthing_start_at'],row['etu_at']]]}
    return [interval for phase in phases for interval in mapping[phase]]

def pair_conflicts(a,b,snapshot,policy):
    errors=[];aid,bid=a['berth_id'],b['berth_id']
    if aid==bid:
        if overlap(a['berthing_start_at'],a['etu_at'],b['berthing_start_at'],b['etu_at']):errors.append('SAME_BERTH_OCCUPANCY')
        first,second=sorted((a,b),key=lambda r:timestamp(r['etb_at']))
        if timestamp(second['work_start_at'])<timestamp(first['switching_end_at']):errors.append('SWITCHING_CONFLICT')
    if aid in policy.get('dock_4142_berths',[]) and bid in policy.get('dock_4142_berths',[]):
        for resource,phases in policy.get('dock_4142_shared_resources',{}).items():
            if any(overlap(*x,*y) for x in resource_intervals(a,phases) for y in resource_intervals(b,phases)):errors.append('4142_SHARED_RESOURCE:'+resource)
    for group in snapshot['workbook_tables']['groups']['rows']:
        members=tokens(group['berth_ids'])
        if aid not in members or bid not in members:continue
        if group['rule_type']=='SAME_CARGO_NOT_CONCURRENT' and a['cargo_group']==b['cargo_group']:
            if overlap(a['etb_at'],a['etu_at'],b['etb_at'],b['etu_at']):errors.append('CARGO_GROUP_OCCUPANCY')
        elif group['rule_type']=='PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP' and aid!=bid:
            if policy['dock_4142_version']=='DISABLED':errors.append('UNSUPPORTED_4142_POLICY');continue
            if not {aid,bid}<=set(policy.get('dock_4142_berths',[])):errors.append('4142_POLICY_MAPPING_MISMATCH');continue
            if policy.get('dock_4142_berthing_resource','MUTEX')=='MUTEX' and overlap(a['berthing_start_at'],a['etb_at'],b['berthing_start_at'],b['etb_at']):errors.append('4142_BERTHING_MANEUVER')
            if any(overlap(*x,*y) for x in a['work_intervals'] for y in b['work_intervals']):errors.append('4142_CARGO_RESOURCE')
            first,second=sorted((a,b),key=lambda r:(timestamp(r['etb_at']),r['call_id']))
            if timestamp(second['etb_at'])<max(timestamp(first['work_end_at']),timestamp(first['etu_at'])-timedelta(minutes=120)):errors.append('4142_EARLY_ETB_BOUNDARY')
            if timestamp(second['work_start_at'])<timestamp(first['switching_end_at']):errors.append('4142_SWITCHING_CONFLICT')
    return sorted(set(errors))


def restriction_conflicts(row,snapshot):
    errors=[]
    for block in snapshot['workbook_tables']['restrictions']['rows']:
        if block['berth_id']!=row['berth_id'] or block.get('cargo_group') not in (None,'',row['cargo_group']):continue
        intervals=list(row['work_intervals'])
        if block['restriction_scope']=='BOTH':intervals.append([row['berthing_start_at'],row['etb_at']])
        if any(overlap(*i,block['start_local'],block['end_local']) for i in intervals):errors.append('BERTH_RESTRICTION')
    return errors


def tug_conflicts(rows,policy):
    if policy.get('tug_capacity') is None:return []
    events={}
    for row in rows:
        for phase,(a,b) in enumerate([(row['berthing_start_at'],row['etb_at']),(row['unberthing_start_at'],row['etu_at'])]):
            a,b=timestamp(a),timestamp(b)
            if a<b:
                events[a]=events.get(a,0)+row['tug_units']['berthing' if phase==0 else 'unberthing'];events[b]=events.get(b,0)-row['tug_units']['berthing' if phase==0 else 'unberthing']
    used=0
    for at,delta in sorted(events.items()):
        used+=delta
        if used>policy['tug_capacity']:return ['TUG_CAPACITY_EXCEEDED']
    return []


def validate_result(snapshot,rows,policy):
    calls={c['call_id']:c for c in snapshot['calls']};berths={b['berth_id']:b for b in snapshot['berths']};issues=[];seen=set()
    from .policy import validate_policy
    try:validate_policy(policy,[b['berth_id'] for b in snapshot['berths'] if b.get('active')=='Y'])
    except (ValueError,KeyError,TypeError) as exc:return {'status':'INVALID','issues':[{'code':'INVALID_POLICY','reason':str(exc)}],'operational_approval':False}
    for row in rows:
        cid=row.get('call_id')
        try:
            if cid in seen:raise ValueError('Duplicate call assignment')
            seen.add(cid)
            expected=replay(calls[cid],berths[row['berth_id']],row['etb_at'],policy,restrictions=snapshot['workbook_tables']['restrictions']['rows'],hose_connection_start_at=row['hose_connection_start_at'],unberthing_start_at=row['unberthing_start_at'])
            if row!=expected:raise ValueError('Phase timestamps differ from independent replay')
            if row['berth_id'] in policy.get('dock_4142_berths',[]):
                for resource,phases in policy.get('dock_4142_shared_resources',{}).items():
                    intervals=resource_intervals(row,phases)
                    if any(overlap(*a,*b) for i,a in enumerate(intervals) for b in intervals[i+1:]):issues.append({'code':'INTRA_VESSEL_SHARED_RESOURCE_CONFLICT','call_id':cid,'resource':resource})
            for code in restriction_conflicts(row,snapshot):issues.append({'code':code,'call_id':cid})
        except (KeyError,ValueError,TypeError) as exc:issues.append({'code':'INVALID_PHASE_ASSIGNMENT','call_id':cid,'reason':str(exc)})
    if not issues:
        for i,a in enumerate(rows):
            for b in rows[i+1:]:
                for code in pair_conflicts(a,b,snapshot,policy):issues.append({'code':code,'call_ids':[a['call_id'],b['call_id']]})
        issues.extend({'code':code} for code in tug_conflicts(rows,policy))
    return {'status':'VALID' if not issues else 'INVALID','issues':issues,'operational_approval':False,
            'scope':'Berth subsystem under supplied synthetic policy; tank inventory and contract settlement not evaluated'}
