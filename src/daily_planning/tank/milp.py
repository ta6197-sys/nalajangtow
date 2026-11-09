"""Pyomo formulation for the isolated TANK_LP_MILP_V1 model.

All quantities are in thousands of bbl; timestamps become exact-second event
intervals. The caller independently verifies every returned solution.
"""
from __future__ import annotations

import time

MODEL_VERSION = 'TANK_LP_MILP_V1'
SCALE_BBL = 1000
FEASIBILITY_TOLERANCE = 1e-7


def build_model(data, mode):
    import pyomo.environ as pyo
    t0 = time.perf_counter()
    tanks, calls, boundaries = data['tanks'], data['calls'], data['boundaries']
    pairs = data['pairs']
    choices = [(i, k) for i, options in pairs.items() for k in options]
    arcs = [(i, k, t) for i, k in choices for t in range(len(boundaries)-1)
            if data['available'](i, boundaries[t], boundaries[t+1])]
    m = pyo.ConcreteModel()
    m.C = pyo.Set(initialize=list(calls), ordered=True)
    m.K = pyo.Set(initialize=list(tanks), ordered=True)
    m.T = pyo.RangeSet(0, len(boundaries)-2)
    m.P = pyo.Set(dimen=2, initialize=choices, ordered=True)
    m.A = pyo.Set(dimen=3, initialize=arcs, ordered=True)
    if mode == 'MILP':
        m.y = pyo.Var(m.P, domain=pyo.Binary)
        m.activation = pyo.Var(m.A, domain=pyo.Binary)
    m.q = pyo.Var(m.A, domain=pyo.NonNegativeReals)
    m.inventory = pyo.Var(m.K, range(len(boundaries)), domain=pyo.Reals)
    m.selection = pyo.ConstraintList();m.limits = pyo.ConstraintList();m.balances = pyo.ConstraintList()
    m.resources = pyo.ConstraintList();m.cargo = pyo.ConstraintList()
    for i in calls:
        if mode == 'MILP': m.selection.add(sum(m.y[i,k] for k in pairs[i]) == 1)
        m.cargo.add(sum(m.q[a] for a in arcs if a[0] == i) == float(calls[i]['remaining'])/SCALE_BBL)
    for i,k,t in arcs:
        duration=(boundaries[t+1]-boundaries[t]).total_seconds()/3600
        maximum=float(data['rate'](i,k))*duration/SCALE_BBL
        if mode == 'MILP':
            m.limits.add(m.q[i,k,t] <= maximum*m.activation[i,k,t])
            m.limits.add(m.activation[i,k,t] <= m.y[i,k])
            if data['fixed_rate'](i,boundaries[t],boundaries[t+1]) is not None:
                fixed=float(data['fixed_rate'](i,boundaries[t],boundaries[t+1]))*duration/SCALE_BBL
                m.limits.add(m.activation[i,k,t] == m.y[i,k])
                m.limits.add(m.q[i,k,t] == fixed*m.y[i,k])
        else:
            m.limits.add(m.q[i,k,t] <= maximum)
            if data['fixed_rate'](i,boundaries[t],boundaries[t+1]) is not None:
                m.limits.add(m.q[i,k,t] == float(data['fixed_rate'](i,boundaries[t],boundaries[t+1]))*duration/SCALE_BBL)
    if mode == 'MILP':
        for t in m.T:
            for k in tanks:
                selected=[m.activation[a] for a in arcs if a[1]==k and a[2]==t]
                if selected: m.resources.add(sum(selected)<=1)
            for berth in sorted({calls[i]['berth_id'] for i in calls}):
                selected=[m.activation[a] for a in arcs if calls[a[0]]['berth_id']==berth and a[2]==t]
                if selected: m.resources.add(sum(selected)<=1)
    for k,tank in tanks.items():
        m.inventory[k,0].fix(float(tank['initial'])/SCALE_BBL)
        for t in range(len(boundaries)):
            m.inventory[k,t].setlb(float(tank['minimum'])/SCALE_BBL)
            m.inventory[k,t].setub(float(tank['maximum'])/SCALE_BBL)
        for t in m.T:
            duration=(boundaries[t+1]-boundaries[t]).total_seconds()/3600
            transfers=sum((1 if calls[a[0]]['direction']=='DISCHARGE' else -1)*m.q[a]
                          for a in arcs if a[1]==k and a[2]==t)
            m.balances.add(m.inventory[k,t+1]==m.inventory[k,t]+transfers+float(tank['flow'])*duration/SCALE_BBL)
    m.objective=pyo.Objective(expr=0.0)
    stats={'construction_seconds':time.perf_counter()-t0,
           'variable_count':sum(1 for _ in m.component_data_objects(pyo.Var,active=True)),
           'binary_variable_count':sum(1 for v in m.component_data_objects(pyo.Var,active=True) if v.is_binary()),
           'constraint_count':sum(1 for _ in m.component_data_objects(pyo.Constraint,active=True))}
    return m,stats
