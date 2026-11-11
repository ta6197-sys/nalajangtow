"""Validated adapter between integrated V5 optimization and rolling state."""
from .v5_settlement import price_v5_schedule
from . import v5_physical
from time import monotonic


def optimize_integrated_window(snapshot, work, *, as_of_at, settlement_records, pricing_policy, clock_policy, **kwargs):
    start=monotonic();budget=kwargs['time_limit_seconds']
    run=v5_physical.schedule_v5(snapshot,as_of_at=as_of_at,settlement_records=settlement_records,
        pricing_policy=pricing_policy,clock_policy=clock_policy,candidate_generation_mode='AUTO_ADAPTIVE_CANDIDATES',
        objective_mode='MIN_TOTAL_DEMURRAGE',inventory_constraint_mode='EXACT_EVENT_BALANCE_V1',
        candidate_improvement_mode='COST_AWARE_V1',**kwargs)
    if run.get('status')!='PHYSICAL_AND_INVENTORY_VALID' or run.get('combined_validation')!='PHYSICAL_AND_INVENTORY_VALID':
        return {'status':'WINDOW_NOT_VALIDATED','reason':run.get('status'),'issues':run.get('issues',[]),'optimizer_result':run}
    rows=run['selected_schedule'];tank=run['inventory_result']
    price=price_v5_schedule(work,rows,as_of_at=as_of_at,settlement_records=settlement_records,pricing_policy=pricing_policy,clock_policy=clock_policy)
    if monotonic()-start>=budget:return {'status':'WINDOW_NOT_VALIDATED','reason':'TIME_LIMIT_BEFORE_ROLLING_SETTLEMENT_VALIDATION','issues':[],'optimizer_result':run}
    if price['status']!='SETTLED' or price['total_demurrage_cents']!=run['total_projected_demurrage_cents']:
        return {'status':'WINDOW_NOT_VALIDATED','reason':'SETTLEMENT_MISMATCH_OR_UNRESOLVED','issues':price['issues'],'optimizer_result':run}
    return {'status':'VALIDATED','selected_schedule':rows,'physical_validation':run['physical_validation'],
        'inventory_validation':run['inventory_validation'],'settlement_validation':price['status'],'tank_result':tank['tank_result'],
        'vessel_demurrage_breakdown':price['vessel_demurrage_breakdown'],'total_demurrage_cents':price['total_demurrage_cents'],
        'solver_status':run['solver_status'],'optimality_scope':run['optimality_scope'],
        'candidate_domain_fingerprint':run['candidate_domain_fingerprint'],'runtime_seconds':run['runtime_seconds'],
        'simulation_only':run['simulation_only'],'optimizer_result':run}
