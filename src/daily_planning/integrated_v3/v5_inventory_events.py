"""Exact option-level inventory inputs; no optimization or schedule approval."""
from fractions import Fraction
from ..integrated_input.validator import number, timestamp, tokens
from ..integrated_input.provenance import canonical, digest
from .coordinator import build_v5_transfer


def _seconds(a, b):
    delta=b-a
    if delta.microseconds: raise ValueError('Subsecond intervals are unsupported')
    return delta.days*86400+delta.seconds


def prepare_inventory_events(snapshot, options, *, start_at, end_at, carried_transfers=(), commitments=(), physical_commitments=()):
    """Return Fraction contributions indexed by candidate and tank event.

    Carried and protected transfers must be represented by their unchanged full
    physical option. Only their unexecuted portion contributes after start.
    """
    start,end=timestamp(start_at),timestamp(end_at)
    if end<=start: raise ValueError('Invalid projection horizon')
    calls={c['call_id']:c for c in snapshot['calls']};berths={b['berth_id']:b for b in snapshot['berths']};tanks={t['tank_id']:t for t in snapshot['tanks']}
    if len(calls)!=len(snapshot['calls']) or len(berths)!=len(snapshot['berths']) or len(tanks)!=len(snapshot['tanks']):raise ValueError('Duplicate identifiers')
    carried={x['call_id']:x for x in carried_transfers};protected={x['call_id']:x for x in commitments}
    if len(carried)!=len(carried_transfers) or len(protected)!=len(commitments):raise ValueError('Duplicate protected or carried transfer')
    physical={x['call_id']:x for x in physical_commitments}
    if len(physical)!=len(physical_commitments):raise ValueError('Duplicate protected physical commitment')
    events={tid:{start,end} for tid in tanks};flows={tid:[] for tid in tanks}
    for flow in snapshot['fixed_process_flows']['segments']:
        tid=flow['tank_id'];a,b=timestamp(flow['start_at']),timestamp(flow['end_at']);rate=number(flow['fixed_net_flow_bbl_per_hour'])
        if tid not in tanks or b<=a:raise ValueError('Invalid external flow: '+str(tid))
        if b>start and a<end:
            a=max(a,start);b=min(b,end);flows[tid].append((a,b,rate));events[tid].update((a,b))
    for tid,rows in flows.items():
        cursor=start
        for a,b,_ in sorted(rows):
            if a!=cursor:raise ValueError('External flow gap or overlap: '+tid)
            cursor=b
        if cursor!=end:raise ValueError('Incomplete external flow coverage: '+tid)
    profiles=[];seen_protected=set()
    for row in options:
        cid=row['call_id'];bid=row['berth_id'];c=calls[cid];b=berths[bid]
        if cid in physical and canonical(row)!=canonical(physical[cid]):raise ValueError('Protected physical commitment changed: '+cid)
        matches=[t for t in tanks.values() if t['cargo_group']==c['cargo_group']]
        if len(matches)!=1:raise ValueError('Unique tank required: '+cid)
        tank=matches[0];tid=tank['tank_id'];direction=c['direction']
        if b.get('active')!='Y' or tid not in tokens(b['connected_tank_ids']) or bid not in tokens(tank['connected_berth_ids']) or c['cargo_group'] not in tokens(b['allowed_cargo_groups']) or direction not in ('LOAD','DISCHARGE') or direction not in tokens(b['allowed_operations']):raise ValueError('Invalid tank connection or direction: '+cid)
        work_end=timestamp(row.get('work_end_at') or row['work_intervals'][-1][1])
        if work_end<=start:
            if cid not in physical:raise ValueError('Completed cargo requires protected physical commitment: '+cid)
            profiles.append({'call_id':cid,'berth_id':bid,'tank_id':tid,'direction':direction,'work_intervals':row['work_intervals'],'segments':[],'quantity_bbl':Fraction(0),'completed':True});continue
        transfer=build_v5_transfer(row,calls,berths);transfer['tank_id']=tid
        if timestamp(transfer['end_at'])>end:raise ValueError('Transfer exceeds projection: '+cid)
        if number(c['cargo_quantity_mt'])*number(c['physical']['bbl_per_mt'])!=number(c['cargo_volume_bbl']):raise ValueError('Cargo conversion mismatch: '+cid)
        if cid in protected and canonical(protected[cid])!=canonical(transfer):raise ValueError('Protected transfer changed: '+cid)
        if cid in protected:seen_protected.add(cid)
        completed=Fraction(0);total=Fraction(0)
        for piece in transfer['segments']:
            a,z=timestamp(piece['start_at']),timestamp(piece['end_at']);rate=number(piece['rate_bbl_per_hour']);total+=rate*Fraction(_seconds(a,z),3600)
            if a<start:completed+=rate*Fraction(_seconds(a,min(z,start)),3600)
            if z>start and a<end:events[tid].update((max(a,start),min(z,end)))
        if total!=number(c['cargo_volume_bbl']):raise ValueError('Transfer quantity mismatch: '+cid)
        if timestamp(transfer['start_at'])<start:
            carry=carried.get(cid)
            if not carry or carry.get('transfer_fingerprint')!=digest(canonical(transfer).encode()) or timestamp(carry['at'])!=start or carry.get('tank_id')!=tid or carry.get('berth_id')!=bid or number(carry['remaining_bbl'])!=total-completed:raise ValueError('Invalid carried transfer: '+cid)
        elif cid in carried:raise ValueError('Unexpected carried transfer: '+cid)
        profiles.append({'call_id':cid,'berth_id':bid,'tank_id':tid,'direction':direction,'work_intervals':row['work_intervals'],'segments':transfer['segments'],'quantity_bbl':total,'transfer':transfer,'completed':False})
    if set(carried)-{p['call_id'] for p in profiles} or set(protected)-seen_protected:raise ValueError('Protected or carried transfer missing')
    result={}
    for tid in tanks:
        points=sorted(events[tid]);external=[]
        for t in points:
            external.append(sum((rate*Fraction(_seconds(a,min(b,t)),3600) for a,b,rate in flows[tid] if a<t),Fraction(0)))
        result[tid]={'events':points,'external_cumulative_bbl':external}
    for p in profiles:
        points=result[p['tank_id']]['events'];sign=1 if p['direction']=='DISCHARGE' else -1
        parsed=[(timestamp(x['start_at']),timestamp(x['end_at']),number(x['rate_bbl_per_hour'])) for x in p['segments']]
        p['signed_cumulative_bbl']=[sign*sum((r*Fraction(_seconds(max(a,start),min(z,t)),3600) for a,z,r in parsed if max(a,start)<min(z,t)),Fraction(0)) for t in points]
        p['receipt_rates_bbl_per_hour']=[sum((r for a,z,r in parsed if a<=x and y<=z),Fraction(0)) if sign==1 else Fraction(0) for x,y in zip(points,points[1:])]
        p['dispatch_rates_bbl_per_hour']=[sum((r for a,z,r in parsed if a<=x and y<=z),Fraction(0)) if sign==-1 else Fraction(0) for x,y in zip(points,points[1:])]
    return {'tanks':result,'options':profiles,'projection_start':start,'projection_end':end}
