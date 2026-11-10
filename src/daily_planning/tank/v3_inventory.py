"""Independent exact V3 tank balances. Never changes source or approves a schedule."""
from collections import defaultdict
from datetime import timedelta
from fractions import Fraction
from ..integrated_input.validator import number, timestamp, tokens
from ..integrated_input.provenance import canonical, digest

VERSION = 'V3_TANK_INVENTORY_V1'

def _secs(a, b):
    return int((b-a).total_seconds())

def _q(x):
    return str(x)

def validate_v3_inventory(snapshot, transfers, *, start_at, end_at, initial_inventory=None, same_tank_policy=None, carried_transfers=None, commitments=None):
    """Project fixed transfers; quantities and signed process rates are exact bbl/hour.

    A transfer has call_id, berth_id, tank_id, start_at and end_at. Rate is the
    smaller supplied call/berth MT/h rate converted with source bbl/MT. The
    operation must transfer the complete original cargo at that constant rate.
    """
    issues=[]
    def issue(code, reason, **kw): issues.append(dict(code=code, reason=str(reason), **kw))
    try:
        start,end=timestamp(start_at),timestamp(end_at)
        if same_tank_policy not in (None,'ALLOW','FORBID'): raise ValueError('Unknown same-tank concurrency policy')
        if end<=start: raise ValueError('Invalid projection horizon')
        tanks={t['tank_id']:t for t in snapshot['tanks']}; calls={c['call_id']:c for c in snapshot['calls']}; berths={b['berth_id']:b for b in snapshot['berths']}
        if any(len(d)!=len(snapshot[k]) for d,k in ((tanks,'tanks'),(calls,'calls'),(berths,'berths'))): raise ValueError('Duplicate source identity')
        flow=snapshot['fixed_process_flows']; by_tank=defaultdict(list)
        for f in flow['segments']:
            if f['tank_id'] not in tanks: raise ValueError('Unknown flow tank '+str(f['tank_id']))
            a,b=timestamp(f['start_at']),timestamp(f['end_at']); rate=number(f['fixed_net_flow_bbl_per_hour'])
            if b<=a: raise ValueError('Invalid flow interval')
            by_tank[f['tank_id']].append((a,b,rate))
        initial_at=timestamp(flow['inventory_as_of_at'])
        if initial_inventory is None and start!=initial_at: raise ValueError('Carry-in inventory required when start differs from inventory_as_of_at')
        if initial_inventory is not None and set(initial_inventory)!=set(tanks): raise ValueError('Carry-in inventory required for every tank')
        if commitments is not None:
            if not isinstance(commitments,(list,tuple)): raise ValueError('Commitments must be a list')
            expected={row['call_id']:row for row in commitments}
            if len(expected)!=len(commitments): raise ValueError('Duplicate protected commitment')
            supplied={row['call_id']:row for row in transfers}
            for cid,row in expected.items():
                if cid not in supplied or canonical(row)!=canonical(supplied[cid]):
                    issue('COMMITMENT_CHANGED','Protected transfer missing or modified',call_id=cid)
            if issues:
                return {'model_version':VERSION,'status':'INVALID_INPUT','issues':issues,'operational_approval':False}
        prepared=[]; seen=set(); schedules=[]
        carried_by_id={row['call_id']:row for row in (carried_transfers or [])}
        if len(carried_by_id)!=len(carried_transfers or []): raise ValueError('Duplicate carried transfer')
        for x in transfers:
            cid=x['call_id']; tid=x['tank_id']; bid=x['berth_id']
            if cid in seen: raise ValueError('Duplicate vessel call '+cid)
            seen.add(cid); c=calls[cid]; t=tanks[tid]; b=berths[bid]
            if bid not in tokens(t['connected_berth_ids']) or tid not in tokens(b['connected_tank_ids']): raise ValueError('Incompatible berth-to-tank connection '+cid)
            if c['cargo_group']!=t['cargo_group'] or c['cargo_group'] not in tokens(b['allowed_cargo_groups']): raise ValueError('Incompatible cargo '+cid)
            if c['direction'] not in ('LOAD','DISCHARGE') or c['direction'] not in tokens(b['allowed_operations']): raise ValueError('Unsupported cargo direction '+cid)
            if c.get('fixed_etb_is_hard') and (not x.get('etb_at') or timestamp(x['etb_at'])!=timestamp(c['fix_etb_at'])): raise ValueError('FIX ETB missing or changed '+cid)
            a,z=timestamp(x['start_at']),timestamp(x['end_at'])
            if z<=a or z<=start or a>=end: raise ValueError('Transfer does not intersect projection or invalid '+cid)
            ratio=number(c['physical']['bbl_per_mt']); vol=number(c['cargo_volume_bbl']); mass=number(c['cargo_quantity_mt'])
            if ratio<=0 or vol<=0 or mass*ratio!=vol: raise ValueError('Invalid source mass/volume relationship '+cid)
            rates=[number(v) for v in (c.get('transfer_rate_mt_h'),b.get('max_transfer_rate_mt_h')) if v is not None and v!='']
            if not rates or any(v<=0 for v in rates): raise ValueError('Missing or invalid vessel/berth rate '+cid)
            limit=min(rates)*ratio
            pieces=x.get('segments')
            if pieces is None: pieces=[{'start_at':x['start_at'],'end_at':x['end_at'],'rate_bbl_per_hour':str(limit)}]
            if not pieces: raise ValueError('Empty transfer segments '+cid)
            parsed=[]; last=a; total=Fraction(0); completed=Fraction(0)
            for piece in pieces:
                p,q=timestamp(piece['start_at']),timestamp(piece['end_at']); rate=number(piece['rate_bbl_per_hour'])
                if p<last or p<a or q>z or q<=p or rate<=0 or rate>limit: raise ValueError('Invalid or excessive transfer segment '+cid)
                last=q; amount=rate*Fraction(_secs(p,q),3600); total+=amount
                if p<start: completed+=rate*Fraction(_secs(p,min(q,start)),3600)
                parsed.append((p,q,rate))
            if parsed[0][0]!=a or parsed[-1][1]!=z or total!=vol: raise ValueError('Cargo quantity does not exactly match transfer segments '+cid)
            if a<start:
                carry=carried_by_id.get(cid)
                if not carry or carry.get('berth_id')!=bid or carry.get('tank_id')!=tid or timestamp(carry.get('at'))!=start or number(carry.get('remaining_bbl'))!=vol-completed or carry.get('transfer_fingerprint')!=digest(canonical(x).encode()):
                    raise ValueError('Missing or inconsistent carried transfer '+cid)
            elif cid in carried_by_id: raise ValueError('Unexpected carried transfer '+cid)
            schedules.append((x,a,z,limit,vol,c['direction'],parsed))
            for p,q,rate in parsed:
                if q>start and p<end: prepared.append((x,max(p,start),min(q,end),rate,vol,c['direction']))
        if set(carried_by_id)-seen: raise ValueError('Carried transfer absent from input')
        for i,(x,a,b,*_) in enumerate(prepared):
            for y,c,d,*_ in prepared[i+1:]:
                if x['call_id']!=y['call_id'] and x['tank_id']==y['tank_id'] and max(a,c)<min(b,d):
                    if same_tank_policy not in ('ALLOW','FORBID'): issue('PENDING_SAME_TANK_CONCURRENCY','Same-tank concurrency policy is unspecified',tank_id=x['tank_id'],call_ids=[x['call_id'],y['call_id']])
                    elif same_tank_policy=='FORBID': issue('TANK_CONCURRENCY_CONFLICT','Concurrent transfers forbidden by policy',tank_id=x['tank_id'],call_ids=[x['call_id'],y['call_id']])
        series={}; totals={}; extrema={}; carried={}
        boundary=start+timedelta(hours=24)
        for tid,t in tanks.items():
            fs=sorted(by_tank[tid]); relevant=[(a,b,r) for a,b,r in fs if b>start and a<end]
            cursor=start
            for a,b,r in relevant:
                if max(a,start)!=cursor: raise ValueError('Flow gap or overlap for '+tid+' at '+cursor.isoformat())
                cursor=min(b,end)
            if cursor!=end: raise ValueError('Incomplete flow coverage for '+tid)
            current=number(initial_inventory[tid] if initial_inventory is not None else t['initial_inventory_bbl']); lo=number(t.get('min_inventory_bbl','0')); hi=number(t['capacity_bbl'])
            if lo<0 or hi<lo: raise ValueError('Invalid tank bounds '+tid)
            events={start,end}
            report_at=start+timedelta(minutes=30)
            while report_at<end:
                events.add(report_at); report_at+=timedelta(minutes=30)
            if start<boundary<end: events.add(boundary)
            for a,b,_ in relevant: events.update((max(a,start),min(b,end)))
            for x,a,b,*_ in prepared:
                if x['tank_id']==tid: events.update((a,b))
            events=sorted(events); points=[{'at':start.isoformat(),'inventory_bbl':_q(current)}]; minimum=maximum=current; external=Fraction(0)
            if not lo<=current<=hi: issue('INVENTORY_OUT_OF_BOUNDS','Initial inventory outside bounds',tank_id=tid,at=start.isoformat(),inventory_bbl=_q(current))
            for a,b in zip(events,events[1:]):
                flowrate=next(r for x,y,r in relevant if x<=a and b<=y)
                net=flowrate; external+=flowrate*Fraction(_secs(a,b),3600)
                for x,x0,x1,r,v,direction in prepared:
                    if x['tank_id']==tid and x0<=a and b<=x1: net+=r if direction=='DISCHARGE' else -r
                current+=net*Fraction(_secs(a,b),3600); minimum=min(minimum,current);maximum=max(maximum,current)
                points.append({'at':b.isoformat(),'inventory_bbl':_q(current)})
                if not lo<=current<=hi: issue('INVENTORY_OUT_OF_BOUNDS','Tank inventory exceeds operating bounds',tank_id=tid,at=b.isoformat(),inventory_bbl=_q(current))
                if b==boundary: carried[tid]=_q(current)
            series[tid]=points;totals[tid]=_q(external);extrema[tid]={'minimum_bbl':_q(minimum),'maximum_bbl':_q(maximum)}
        carry_jobs=[]
        if boundary<=end:
            for x,a,z,limit,vol,direction,parts in schedules:
                if a<boundary<z:
                    completed=sum((rate*Fraction(_secs(p,min(q,boundary)),3600) for p,q,rate in parts if p<boundary),Fraction(0))
                    carry_jobs.append({'at':boundary.isoformat(),'call_id':x['call_id'],'berth_id':x['berth_id'],'tank_id':x['tank_id'],'remaining_bbl':_q(vol-completed),'transfer_fingerprint':digest(canonical(x).encode())})
        return {'model_version':VERSION,'status':'VALID' if not issues else 'INVALID','issues':issues,'source_fingerprint':digest(canonical(snapshot).encode()),'policy_fingerprint':digest(canonical({'same_tank_policy':same_tank_policy}).encode()),'assignments':[dict(call_id=x['call_id'],berth_id=x['berth_id'],tank_id=x['tank_id'],start_at=a.isoformat(),end_at=b.isoformat(),rate_bbl_per_hour=_q(r),quantity_bbl=_q(v),segments=[{'start_at':p.isoformat(),'end_at':q.isoformat(),'rate_bbl_per_hour':_q(rate)} for p,q,rate in parts]) for x,a,b,r,v,d,parts in schedules], 'inventories':series,'inventory_extrema':extrema,'external_net_flow_totals_bbl':totals,'carry_at':boundary.isoformat() if boundary<=end else None,'carried_inventory_bbl':carried,'carried_transfers':carry_jobs,'commitment_validation':'NOT_PROVIDED' if commitments is None else 'MATCHED', 'pending_checks':['BERTH_PHASES','WEATHER','CONTRACT_SETTLEMENT'] + (['COMMITMENTS_NOT_PROVIDED'] if commitments is None else []), 'operational_approval':False}
    except (ValueError,KeyError,TypeError,ZeroDivisionError,StopIteration) as exc:
        return {'model_version':VERSION,'status':'INVALID_INPUT','issues':[{'code':'INVALID_INPUT','reason':str(exc)}],'operational_approval':False}
