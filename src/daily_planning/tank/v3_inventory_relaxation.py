"""Heuristic fixed-transfer inventory inequalities for V3 tank selection.

Floating-point inequalities are a search guide only. An infeasible heuristic
model is always retried without these inequalities; accepted candidates still
undergo exact independent inventory replay.
"""
from fractions import Fraction
from ..integrated_input.validator import number,timestamp

VERSION='V3_INVENTORY_RELAXATION_V1'


def add_inventory_relaxation(model, snapshot, transfers, pairs, *, start_at, end_at, initial_inventory):
    import pyomo.environ as pyo
    start,end=timestamp(start_at),timestamp(end_at);calls={x['call_id']:x for x in snapshot['calls']};tanks={x['tank_id']:x for x in snapshot['tanks']}
    if initial_inventory is None:
        if start!=timestamp(snapshot['fixed_process_flows']['inventory_as_of_at']):raise ValueError('Missing initial inventory')
        initial_inventory={k:v['initial_inventory_bbl'] for k,v in tanks.items()}
    if set(initial_inventory)!=set(tanks):raise ValueError('Incomplete initial inventory')
    flows={k:[] for k in tanks}
    for row in snapshot['fixed_process_flows']['segments']:
        if row['tank_id'] in flows:
            a,b=timestamp(row['start_at']),timestamp(row['end_at'])
            if b>start and a<end:flows[row['tank_id']].append((a,b,number(row['fixed_net_flow_bbl_per_hour'])))
    parts={};directions={}
    for i,row in enumerate(transfers):
        if 'segments' not in row:raise ValueError('Explicit fixed transfer segments required for heuristic')
        parts[i]=[(timestamp(x['start_at']),timestamp(x['end_at']),number(x['rate_bbl_per_hour'])) for x in row['segments']]
        directions[i]=1 if calls[row['call_id']]['direction']=='DISCHARGE' else -1
    model.inventory_heuristic=pyo.ConstraintList();count=0
    for tid,tank in tanks.items():
        eligible=[i for i,k in pairs if k==tid]
        if not eligible:continue
        events={start,end}
        for a,b,_ in flows[tid]:events.update((max(a,start),min(b,end)))
        for i in eligible:
            for a,b,_ in parts[i]:
                if b>start and a<end:events.update((max(a,start),min(b,end)))
        for at in sorted(events):
            base=number(initial_inventory[tid])
            for a,b,rate in flows[tid]:
                left=max(a,start);right=min(b,at)
                if right>left:base+=rate*Fraction(int((right-left).total_seconds()),3600)
            terms=[]
            for i in eligible:
                volume=Fraction(0)
                for a,b,rate in parts[i]:
                    left=max(a,start);right=min(b,at)
                    if right>left:volume+=rate*Fraction(int((right-left).total_seconds()),3600)
                if volume:terms.append(float(directions[i]*volume)/1000*model.y[i,tid])
            if not terms:continue
            balance=float(base)/1000+sum(terms)
            # Floating-point slacks do not certify feasibility/infeasibility.
            margin=0.000001
            model.inventory_heuristic.add(balance>=float(number(tank['min_inventory_bbl']))/1000-margin)
            model.inventory_heuristic.add(balance<=float(number(tank['capacity_bbl']))/1000+margin);count+=2
    return count
