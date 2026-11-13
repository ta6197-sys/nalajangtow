"""Independent read-only risk labels for validated BALANCED inventory event points.

This module NEVER changes feasibility, candidate selection, settlement or cost.
Stock boundaries are exact, and event samples cannot establish duration.
"""
from __future__ import annotations
from datetime import datetime
from fractions import Fraction

RISK_VERSION = 'V5_INVENTORY_RISK_20_80_10_90_V1'


def exact(value):
    if value is None or isinstance(value, bool):
        raise ValueError('Missing/non-numeric inventory')
    return Fraction(str(value))


def classify(stock_bbl, capacity_bbl):
    stock, capacity = exact(stock_bbl), exact(capacity_bbl)
    if capacity <= 0: raise ValueError('Capacity must be positive')
    ratio = stock / capacity
    if ratio < 0 or ratio > 1:
        code = 'PHYSICAL_INVALID'
    elif ratio < Fraction(1,10):code = 'CRITICAL_LOW'
    elif ratio < Fraction(1,5):code = 'WARNING_LOW'
    elif ratio <= Fraction(4,5):code = 'NORMAL'
    elif ratio <= Fraction(9,10):code = 'WARNING_HIGH'
    else:code = 'CRITICAL_HIGH'
    return {'code':code,'inventory_percent_exact':str(ratio*100),
            'inventory_bbl':str(stock),'physical_valid':0<=ratio<=1,
            'severity': ('HARD_INVALID' if code=='PHYSICAL_INVALID' else
                         'CRITICAL' if code.startswith('CRITICAL') else
                         'WARNING' if code.startswith('WARNING') else 'NORMAL'),
            'direction':'LOW' if code.endswith('LOW') or ratio < 0 else 'HIGH' if code.endswith('HIGH') or ratio > 1 else 'NONE'}


def classify_inventory_points(tanks, inventories, *, version=RISK_VERSION):
    """Consumes validate_v5_inventory result['inventories'] after VALID replay."""
    if version!=RISK_VERSION:raise ValueError('Unsupported inventory risk version')
    ids=[t['tank_id'] for t in tanks]
    if not ids or len(ids)!=len(set(ids)) or set(ids)!=set(inventories):
        raise ValueError('Risk series must cover exactly known tanks')
    changes=[];extrema=[];counts={};classified_points=[]
    for tank in sorted(tanks,key=lambda x:x['tank_id']):
        tid=tank['tank_id'];rows=inventories[tid]
        if not isinstance(rows,list) or not rows:raise ValueError('Empty risk event series')
        prev_time=None;prev_band=None;observations=[]
        for row in rows:
            stamp=datetime.fromisoformat(row['at'])
            if stamp.utcoffset() is None or (prev_time is not None and stamp<=prev_time):
                raise ValueError('Invalid/unsorted inventory event')
            prev_time=stamp
            band=classify(row['inventory_bbl'],tank['capacity_bbl'])
            if not band['physical_valid']:raise ValueError('Physical stock violation, not a warning')
            observations.append((exact(row['inventory_bbl']),row['at'],band))
            classified_points.append({'tank_id':tid,'at':row['at'],**band})
            counts[band['code']]=counts.get(band['code'],0)+1
            if band['code']!=prev_band:
                changes.append({'tank_id':tid,'at':row['at'],**band})
            prev_band=band['code']
        minimum=min(observations,key=lambda x:x[0]);maximum=max(observations,key=lambda x:x[0])
        extrema.append({'tank_id':tid,
                        'minimum':{'at':minimum[1],**minimum[2]},
                        'maximum':{'at':maximum[1],**maximum[2]}})
    return {'risk_policy_version':version,'risk_affects_feasibility':False,'risk_affects_objective':False,
            'scope':'VALIDATED_EVENT_POINTS_ONLY','duration':'NOT_DETERMINED',
            'severity_point_counts':dict(sorted(counts.items())),
            'severity_transitions':changes,'classified_event_points':classified_points,
            'extrema_by_tank':extrema}
