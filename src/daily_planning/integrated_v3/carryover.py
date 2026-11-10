"""Read-only reconciliation of a tank projection across its 24-hour boundary."""
from fractions import Fraction
from ..integrated_input.provenance import canonical,digest
from ..integrated_input.validator import timestamp,number
from ..tank.v3_inventory import validate_v3_inventory

VERSION='V3_TANK_CARRY_RECONCILIATION_V1'


def reconcile_projection(snapshot, prior, *, same_tank_policy=None):
    """Reproject the remaining original plan; this is not an execution step.

    The prior must be a successful complete V3 tank projection with its original
    transfer definitions and the 24-hour carried state. No observations or
    operational commitments are written or inferred.
    """
    def failure(code,reason):
        return {'model_version':VERSION,'status':'INVALID_INPUT','issues':[{'code':code,'reason':str(reason)}],'operational_approval':False}
    try:
        if prior['status']!='VALID':raise ValueError('Prior tank projection is not valid')
        if prior['source_fingerprint']!=digest(canonical(snapshot).encode()):raise ValueError('Snapshot fingerprint mismatch')
        if prior['policy_fingerprint']!=digest(canonical({'same_tank_policy':same_tank_policy}).encode()):raise ValueError('Tank concurrency policy fingerprint mismatch')
        boundary=timestamp(prior['carry_at']);end=timestamp(prior['projection_end_at'])
        if boundary>=end:raise ValueError('Prior projection must extend beyond carry boundary')
        definitions=prior['transfer_definitions']
        continuing=[row for row in definitions if timestamp(row['end_at'])>boundary and timestamp(row['start_at'])<end]
        carry=prior['carried_transfers'];inventory=prior['carried_inventory_bbl']
        next_projection=validate_v3_inventory(snapshot,continuing,start_at=boundary.isoformat(),end_at=end.isoformat(),initial_inventory=inventory,carried_transfers=carry,same_tank_policy=same_tank_policy)
        if next_projection['status']!='VALID':return {'model_version':VERSION,'status':'REPROJECTION_FAILED','issues':next_projection['issues'],'operational_approval':False}
        discrepancies=[]
        for tank_id,points in prior['inventories'].items():
            original={timestamp(p['at']):number(p['inventory_bbl']) for p in points if timestamp(p['at'])>=boundary}
            replay={timestamp(p['at']):number(p['inventory_bbl']) for p in next_projection['inventories'][tank_id]}
            if original!=replay:
                for at in sorted(set(original)|set(replay)):
                    if original.get(at)!=replay.get(at):discrepancies.append({'tank_id':tank_id,'at':at.isoformat(),'prior_bbl':str(original.get(at)),'replay_bbl':str(replay.get(at))})
        return {'model_version':VERSION,'status':'RECONCILED' if not discrepancies else 'MISMATCH','carry_at':boundary.isoformat(),'projection_end_at':end.isoformat(),'tank_count':len(prior['inventories']),'continued_calls':[x['call_id'] for x in continuing],'in_progress_calls':[x['call_id'] for x in carry], 'mismatches':discrepancies,'next_projection':next_projection,'scope':'Same projected plan; no physical observations, replanning, confirmation or transactional execution', 'operational_approval':False}
    except (ValueError,KeyError,TypeError,ZeroDivisionError,OverflowError) as exc:return failure('CARRYOVER_INPUT_INVALID',exc)
