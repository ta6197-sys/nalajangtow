"""Exact linear constraint construction for prepared V5 inventory events."""
from fractions import Fraction
from math import gcd, lcm
from ..integrated_input.validator import number


class InventoryScalingError(ValueError):
    """The exact expression cannot be safely represented by CP-SAT."""


def scale_exact(coefficients, lower, upper):
    """Scale a bounded rational linear expression without loss of precision."""
    vals=[Fraction(v) for v in coefficients]+[Fraction(lower),Fraction(upper)]
    denominator=1
    limit=2**63-1
    for value in vals:
        denominator=lcm(denominator,value.denominator)
        if denominator>limit:raise InventoryScalingError('Exact denominator exceeds signed 64-bit range')
    integers=[v.numerator*(denominator//v.denominator) for v in vals]
    divisor=0
    for value in integers:divisor=gcd(divisor,abs(value))
    if divisor>1:integers=[value//divisor for value in integers]
    coeff=integers[:-2];lo,hi=integers[-2:]
    if any(abs(v)>limit for v in integers) or sum(abs(v) for v in coeff)>limit:
        raise InventoryScalingError('Exact integer expression exceeds signed 64-bit range')
    return coeff,lo,hi


def add_inventory_constraints(model, variables, prepared, tanks, *, initial_inventory, option_count):
    """Add exact balances and gross rates; profiles after option_count are fixed."""
    profiles=prepared['options']
    if len(variables)!=option_count or len(profiles)<option_count:raise ValueError('Option/profile mapping mismatch')
    tank_map={t['tank_id']:t for t in tanks}
    if set(initial_inventory)!=set(tank_map) or set(prepared['tanks'])!=set(tank_map):raise ValueError('Inventory tank set mismatch')
    events=balances=throughput=0
    def add(coeff,lo,hi):
        scaled,low,high=scale_exact(coeff,lo,hi)
        model.AddLinearConstraint(sum(c*x for c,x in zip(scaled,variables)),low,high)
    for tid,data in prepared['tanks'].items():
        tank=tank_map[tid];points=data['events'];events+=len(points)
        for j in range(len(points)):
            base=number(initial_inventory[tid])+data['external_cumulative_bbl'][j]
            base+=sum((p['signed_cumulative_bbl'][j] for p in profiles[option_count:] if p['tank_id']==tid),Fraction(0))
            coeff=[p['signed_cumulative_bbl'][j] if p['tank_id']==tid else Fraction(0) for p in profiles[:option_count]]
            add(coeff,number(tank['min_inventory_bbl'])-base,number(tank['capacity_bbl'])-base);balances+=1
        for j in range(len(points)-1):
            for key,field in [('receipt_rates_bbl_per_hour','max_receipt_rate_bbl_per_hour'),('dispatch_rates_bbl_per_hour','max_dispatch_rate_bbl_per_hour')]:
                fixed=sum((p[key][j] for p in profiles[option_count:] if p['tank_id']==tid),Fraction(0))
                coeff=[p[key][j] if p['tank_id']==tid else Fraction(0) for p in profiles[:option_count]]
                # Rates are nonnegative, so zero is a valid lower bound.
                add(coeff,0,number(tank[field])-fixed);throughput+=1
    return {'inventory_event_count':events,'inventory_constraints_count':balances,'throughput_constraints_count':throughput}
