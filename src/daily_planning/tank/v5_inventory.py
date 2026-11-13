"""V5 unique tank mapping and gross throughput checks over exact V3 replay.

External process flows contribute to inventory, but not vessel equipment limits;
shared facility throughput is an unresolved operational policy.
"""
from copy import deepcopy
from fractions import Fraction
from time import perf_counter

from .v3_inventory import validate_v3_inventory
from ..integrated_input.validator import number, timestamp, tokens
from ..integrated_input.provenance import canonical, digest
from ..integrated_v3.v5_inventory_policy import POLICY_VERSION, physical_bounds, operating_warning_summary


def validate_v5_inventory(snapshot, transfers, *, start_at, end_at, initial_inventory=None,
                          carried_transfers=None, commitments=None, same_tank_policy='ALLOW'):
    started=perf_counter(); issues=[]
    def issue(code, reason, **ids):issues.append({'code':code,'reason':str(reason),**ids})
    def fail():
        return {'scenario_id':snapshot.get('scenario_id'),'status':'INVALID','issues':issues,'solver_bypassed':True,
                'runtime_seconds':perf_counter()-started,'scheduling_feasibility':'NOT_EVALUATED',
                'operational_approval':False}
    try:
        if snapshot.get('scenario_id') not in ('V5_3TANK','V5_BALANCED_154'):raise ValueError('Expected V5 scenario')
        start,end=timestamp(start_at),timestamp(end_at)
        if end<=start:raise ValueError('Invalid projection horizon')
        calls={c['call_id']:c for c in snapshot['calls']};berths={b['berth_id']:b for b in snapshot['berths']};tanks={t['tank_id']:t for t in snapshot['tanks']}
        if any(len(m)!=len(snapshot[k]) for m,k in ((calls,'calls'),(berths,'berths'),(tanks,'tanks'))):raise ValueError('Duplicate source identifier')
        if same_tank_policy not in (None,'ALLOW','FORBID'):raise ValueError('Invalid same-tank concurrency policy')
        carried=carried_transfers or []
        carry_ids=[x['call_id'] for x in carried]
        if len(set(carry_ids))!=len(carry_ids):issue('INVALID_CARRYOVER_STATE','Duplicate carried transfer')
        if initial_inventory is not None:
            if set(initial_inventory)!=set(tanks):issue('INVALID_CARRYOVER_STATE','Carry-in must include every tank')
            else:
                for tid,t in tanks.items():
                    try:
                        lo,hi=physical_bounds(t)
                        if not lo<=number(initial_inventory[tid])<=hi:issue('INVALID_CARRYOVER_STATE','Carry-in inventory outside physical bounds',tank_id=tid,at=start.isoformat())
                    except (ValueError,KeyError,TypeError):issue('INVALID_CARRYOVER_STATE','Invalid carry-in inventory',tank_id=tid,at=start.isoformat())
        if initial_inventory is None and start!=timestamp(snapshot['fixed_process_flows']['inventory_as_of_at']):issue('INVALID_CARRYOVER_STATE','Carry-in inventory required',at=start.isoformat())
        mapped=[];seen=set()
        for x in transfers:
            cid=x.get('call_id');bid=x.get('berth_id')
            if cid in seen:issue('INVALID_TRANSFER_QUANTITY','Duplicate transfer',call_id=cid);continue
            seen.add(cid);c=calls.get(cid);b=berths.get(bid)
            if not c or not b:issue('MISSING_TANK_CONNECTION','Unknown call or berth',call_id=cid,berth_id=bid);continue
            matches=[t for t in tanks.values() if t.get('cargo_group')==c.get('cargo_group')]
            if len(matches)!=1:issue('MISSING_TANK_CONNECTION','Cargo group must map to exactly one tank',call_id=cid,berth_id=bid);continue
            t=matches[0];tid=t['tank_id']
            if x.get('tank_id') not in (None,tid):issue('MISSING_TANK_CONNECTION','Specified tank does not match cargo',call_id=cid,tank_id=x['tank_id'],berth_id=bid);continue
            if b.get('active')!='Y' or tid not in tokens(b.get('connected_tank_ids')) or bid not in tokens(t.get('connected_berth_ids')) or c.get('cargo_group') not in tokens(b.get('allowed_cargo_groups')) or c.get('direction') not in ('LOAD','DISCHARGE') or c.get('direction') not in tokens(b.get('allowed_operations')):
                issue('MISSING_TANK_CONNECTION','Berth/tank connection, cargo or operation invalid',call_id=cid,tank_id=tid,berth_id=bid);continue
            y=deepcopy(x);y.pop('eligible_tank_ids',None);y['tank_id']=tid;mapped.append(y)
        if issues:return fail()
        for x in mapped:
            if x['call_id'] in carry_ids:
                old=next(c for c in carried if c['call_id']==x['call_id'])
                if old.get('transfer_fingerprint')!=digest(canonical(x).encode()):
                    issue('INVALID_CARRYOVER_STATE','Protected carried transfer was modified',call_id=x['call_id'],tank_id=x['tank_id'],at=start.isoformat())
        if issues:return fail()
        replay_snapshot=deepcopy(snapshot) if snapshot.get('scenario_id')=='V5_BALANCED_154' else snapshot
        if snapshot.get('scenario_id')=='V5_BALANCED_154':
            for t in replay_snapshot['tanks']:
                effective=number(t['effective_max_inventory_bbl'])
                if effective!=number(t['capacity_bbl'])*Fraction(95,100):raise ValueError('Invalid BALANCED operating inventory maximum')
                lo,hi=physical_bounds(t)
                t['min_inventory_bbl']=str(lo);t['capacity_bbl']=str(hi)
        result=validate_v3_inventory(replay_snapshot,mapped,start_at=start_at,end_at=end_at,initial_inventory=initial_inventory,
            same_tank_policy=same_tank_policy,carried_transfers=carried_transfers,commitments=commitments)
        if snapshot.get('scenario_id')=='V5_BALANCED_154':
            source_view=deepcopy(snapshot)
            claimed=source_view.pop('_verified_source_fingerprint',None)
            if claimed is not None:
                for tank in source_view['tanks']:
                    for key in ('inventory_policy_version','operational_min_inventory_bbl','operational_max_inventory_bbl'):
                        tank.pop(key,None)
            actual=digest(canonical(source_view).encode())
            if claimed is not None and claimed!=actual:raise ValueError('Inventory policy source fingerprint mismatch')
            result['source_fingerprint']=actual
        if result['status']=='INVALID_INPUT':
            for e in result['issues']:
                reason=e.get('reason','');low=reason.lower()
                if e.get('code')=='COMMITMENT_CHANGED':code='INVALID_CARRYOVER_STATE'
                elif 'flow' in low:code='INVALID_FLOW_COVERAGE'
                elif any(w in low for w in ('carry','carried','commitment')):code='INVALID_CARRYOVER_STATE'
                elif 'connection' in low or 'cargo direction' in low:code='MISSING_TANK_CONNECTION'
                else:code='INVALID_TRANSFER_QUANTITY'
                ids={k:v for k,v in e.items() if k not in ('code','reason')}
                for x in mapped:
                    if x['call_id'] in reason:ids.setdefault('call_id',x['call_id']);ids.setdefault('tank_id',x['tank_id'])
                for tid in tanks:
                    if tid in reason:ids.setdefault('tank_id',tid)
                issue(code,reason,**ids)
            return fail()
        for e in result['issues']:
            e=deepcopy(e)
            if e['code']=='PENDING_SAME_TANK_CONCURRENCY':e['code']='POLICY_UNRESOLVED'
            issues.append(e)
        maxima={tid:{'receipt_bbl_per_hour':'0','dispatch_bbl_per_hour':'0'} for tid in tanks}
        intervals=[];reconciliation={}
        for x in mapped:
            c=calls[x['call_id']];b=berths[x['berth_id']]
            pieces=x.get('segments')
            if pieces is None:
                rate=min(number(v) for v in (c.get('transfer_rate_mt_h'),b.get('max_transfer_rate_mt_h')) if v not in (None,''))*number(c['physical']['bbl_per_mt'])
                pieces=[{'start_at':x['start_at'],'end_at':x['end_at'],'rate_bbl_per_hour':str(rate)}]
            quantity=Fraction(0);done=Fraction(0)
            for p in pieces:
                a,z=timestamp(p['start_at']),timestamp(p['end_at']);rate=number(p['rate_bbl_per_hour'])
                quantity+=rate*Fraction(int((z-a).total_seconds()),3600)
                if a<end:done+=rate*Fraction(int((min(z,end)-a).total_seconds()),3600)
                if a<end and z>start:intervals.append((max(a,start),min(z,end),rate,c['direction'],x['tank_id'],x['call_id']))
            reconciliation[x['call_id']]={'expected_bbl':str(number(c['cargo_volume_bbl'])),'segments_bbl':str(quantity),'matched':quantity==number(c['cargo_volume_bbl'])}
        for tid,t in tanks.items():
            relevant=[r for r in intervals if r[4]==tid];events=sorted({q for r in relevant for q in r[:2]})
            receipt_max=dispatch_max=Fraction(0)
            for a,z in zip(events,events[1:]):
                active=[r for r in relevant if r[0]<=a and r[1]>=z]
                receipt=sum((r[2] for r in active if r[3]=='DISCHARGE'),Fraction(0));dispatch=sum((r[2] for r in active if r[3]=='LOAD'),Fraction(0))
                receipt_max=max(receipt_max,receipt);dispatch_max=max(dispatch_max,dispatch)
                for rate,field,code,direction in [(receipt,'max_receipt_rate_bbl_per_hour','RECEIPT_RATE_EXCEEDED','DISCHARGE'),(dispatch,'max_dispatch_rate_bbl_per_hour','DISPATCH_RATE_EXCEEDED','LOAD')]:
                    if rate>number(t[field]):issue(code,'Gross vessel throughput exceeds tank limit',tank_id=tid,at=a.isoformat(),end_at=z.isoformat(),rate_bbl_per_hour=str(rate),limit_bbl_per_hour=str(number(t[field])),call_ids=[r[5] for r in active if r[3]==direction])
            maxima[tid]={'receipt_bbl_per_hour':str(receipt_max),'dispatch_bbl_per_hour':str(dispatch_max)}
        # The replay's built-in carry point is 24 h. Emit an additional carry at
        # the requested projection end, including at non-24-hour boundaries.
        end_carry=[]
        for x in mapped:
            a,z=timestamp(x['start_at']),timestamp(x['end_at'])
            if not a<end<z:continue
            c=calls[x['call_id']];b=berths[x['berth_id']];pieces=x.get('segments')
            if pieces is None:
                rate=min(number(v) for v in (c.get('transfer_rate_mt_h'),b.get('max_transfer_rate_mt_h')) if v not in (None,''))*number(c['physical']['bbl_per_mt'])
                pieces=[{'start_at':x['start_at'],'end_at':x['end_at'],'rate_bbl_per_hour':str(rate)}]
            done=sum((number(p['rate_bbl_per_hour'])*Fraction(int((min(timestamp(p['end_at']),end)-timestamp(p['start_at'])).total_seconds()),3600) for p in pieces if timestamp(p['start_at'])<end),Fraction(0))
            end_carry.append({'at':end.isoformat(),'call_id':x['call_id'],'berth_id':x['berth_id'],'tank_id':x['tank_id'],'remaining_bbl':str(number(c['cargo_volume_bbl'])-done),'transfer_fingerprint':digest(canonical(x).encode())})
        result.update(scenario_id=snapshot.get('scenario_id'),status='VALID' if not issues else 'INVALID',issues=issues,solver_bypassed=True,
            gross_vessel_rate_maxima=maxima,quantity_reconciliation=reconciliation,
            end_state=({'at':end.isoformat(),'inventory_bbl':{tid:points[-1]['inventory_bbl'] for tid,points in result['inventories'].items()},'carried_transfers':end_carry} if not issues else None),
            carryover_state_valid=not issues,
            external_flow_capacity_policy='EXCLUDED_FROM_VESSEL_LIMITS_SHARED_FACILITY_POLICY_UNRESOLVED',
            same_tank_concurrency_policy=same_tank_policy,
            scheduling_feasibility='NOT_EVALUATED',runtime_seconds=perf_counter()-started)
        if snapshot.get('scenario_id')=='V5_BALANCED_154' and result['status']=='VALID' and any(t.get('inventory_policy_version')==POLICY_VERSION for t in snapshot['tanks']):
            result.update(operating_warning_summary(snapshot['tanks'],result['inventories']))
        return result
    except (ValueError,KeyError,TypeError,ZeroDivisionError,OverflowError) as exc:
        issue('INVALID_TRANSFER_QUANTITY',exc);return fail()
