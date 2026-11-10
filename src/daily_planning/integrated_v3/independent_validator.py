"""Independent read-only validation of a costed V3 simulation incumbent."""
from datetime import timedelta
from ..integrated_input.validator import timestamp
from ..integrated_input.provenance import canonical,digest
from ..physical_scheduling.validator import validate_result
from ..tank.v3_inventory import validate_v3_inventory

VERSION='V3_SIMULATION_INCUMBENT_VALIDATOR_V1'


def validate_simulation_candidate(snapshot, candidate, *, policy, as_of_at, commitments=(), completed_rows=(),
                                  initial_inventory=None, carried_transfers=None, tank_commitments=None,
                                  same_tank_policy=None, settlement_records=(), pricing_policy='EXPLICIT_EVENTS_V1'):
    issues=[]
    def issue(code,reason):issues.append({'code':code,'reason':str(reason)})
    try:
        if candidate['source_fingerprint']!=digest(canonical(snapshot).encode()):issue('SOURCE_FINGERPRINT_MISMATCH','Source changed')
        if candidate['policy_fingerprint']!=digest(canonical(policy).encode()):issue('POLICY_FINGERPRINT_MISMATCH','Policy changed')
        rows=candidate['berth_schedule'];by_id={row['call_id']:row for row in rows}
        if len(by_id)!=len(rows):issue('DUPLICATE_CALL','Physical rows contain duplicates')
        physical=validate_result(snapshot,rows,policy)
        if physical['status']!='VALID':issues.extend(physical['issues'])
        for row in commitments:
            if by_id.get(row['call_id'])!=row:issue('COMMITMENT_CHANGED',row['call_id'])
        if {x['call_id'] for x in completed_rows}&set(by_id):issue('COMPLETED_RESCHEDULED','Completed call appears again')
        completed_ids={x['call_id'] for x in completed_rows};locked_ids={x['call_id'] for x in commitments}
        horizon=timestamp(as_of_at)+timedelta(hours=72)
        in_scope={c['call_id'] for c in snapshot['calls'] if c['call_id'] not in completed_ids and timestamp(c['fix_etb_at'] if c.get('fixed_etb_is_hard') else c['eta_at'])<horizon}
        if set(by_id)!=(in_scope|locked_ids):issue('CALL_COVERAGE_MISMATCH','Selected calls differ from in-scope calls and explicit commitments')
        definitions=candidate['tank_validation']['transfer_definitions'];by_transfer={x['call_id']:x for x in definitions}
        if len(by_transfer)!=len(definitions):issue('DUPLICATE_TRANSFER','Duplicate tank transfer')
        boundary=timestamp(as_of_at)
        expected={row['call_id'] for row in rows if timestamp(row['work_end_at'])>boundary}
        if set(by_transfer)!=expected:issue('TRANSFER_COVERAGE_MISMATCH','Tank transfers do not match active physical work')
        for cid in expected&set(by_transfer):
            row=by_id[cid];transfer=by_transfer[cid]
            if transfer['berth_id']!=row['berth_id']:issue('TRANSFER_BERTH_MISMATCH',cid);continue
            segments=transfer.get('segments') or [{'start_at':transfer['start_at'],'end_at':transfer['end_at']}]
            merged=[]
            for seg in segments:
                a,b=timestamp(seg['start_at']),timestamp(seg['end_at'])
                if merged and merged[-1][1]==a:merged[-1]=(merged[-1][0],b)
                else:merged.append((a,b))
            if merged!=[(timestamp(a),timestamp(b)) for a,b in row['work_intervals']]:issue('TRANSFER_TIME_MISMATCH',cid)
        original=candidate['tank_validation']
        tank=validate_v3_inventory(snapshot,definitions,start_at=original['projection_start_at'],end_at=original['projection_end_at'],initial_inventory=initial_inventory,carried_transfers=carried_transfers,commitments=tank_commitments,same_tank_policy=same_tank_policy)
        if tank['status']!='VALID':issues.extend(tank['issues'])
        elif tank['inventories']!=original['inventories'] or tank['carried_inventory_bbl']!=original['carried_inventory_bbl']:issue('INVENTORY_REPLAY_MISMATCH','Replayed inventory differs')
        from .optimizer import _price_schedule
        price=_price_schedule(snapshot,rows,settlement_records,as_of_at=as_of_at,pricing_policy=pricing_policy)
        if price['status']!='SETTLED':issues.extend(price['issues'])
        elif price['total_demurrage_cents']!=candidate['total_demurrage_cents']:issue('DEMURRAGE_REPLAY_MISMATCH','Total cost differs')
        if price['status']=='SETTLED' and price['settlements']!=candidate.get('settlements'):issue('SETTLEMENT_DETAIL_MISMATCH','Replayed vessel settlement details differ')
        return {'model_version':VERSION,'status':'VALID' if not issues else 'INVALID','issues':issues,'scope':'Independent simulation replay; actual weather and operator approval remain external', 'operational_approval':False}
    except (ValueError,KeyError,TypeError,OverflowError,ZeroDivisionError) as exc:
        return {'model_version':VERSION,'status':'INVALID','issues':[{'code':'VALIDATION_INPUT_INVALID','reason':str(exc)}],'operational_approval':False}
