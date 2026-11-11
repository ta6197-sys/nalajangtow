"""Small, synthetic comparison; never changes source input or claims global optimality."""
import copy
import json
import time
from unittest.mock import patch

from tests.test_v5_cost_aware import make_case, run_case, exhaustive
from tests.test_v5_physical_integration import START
from src.daily_planning.integrated_v3.v5_cost_search import optimize_v5

for n in (2, 3):
    snapshot, policy, times = make_case(n)
    started = time.perf_counter()
    direct = run_case(snapshot, policy, times)
    direct_time = time.perf_counter() - started
    oracle = exhaustive(snapshot, policy, direct)
    with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot', side_effect=lambda *args: copy.deepcopy(snapshot)), \
         patch('src.daily_planning.integrated_input.v5.validate_v5', return_value={'data_preflight_pass': True, 'errors': []}), \
         patch('src.daily_planning.integrated_v3.v5_physical.prepare_pilotage_policy', side_effect=lambda s, c, e, p, *args: (p, None)):
        started = time.perf_counter()
        old = optimize_v5(snapshot, data_dir='SYNTHETIC_TEST', call_ids=list(times), as_of_at=START,
                          policy=policy, pilotage_context={}, candidate_generation_mode='MANUAL_CANDIDATES',
                          candidate_etbs=times, pricing_policy='CLEAR_WEATHER_BASELINE_V1', settlement_records=[],
                          max_cost_candidates=5, time_limit_seconds=30)
        old_time = time.perf_counter() - started
    print(json.dumps({'calls': n, 'physical_options': direct['physical_option_count'],
        'oracle_feasible_schedules': len(oracle), 'oracle_minimum_cents': oracle[0],
        'direct_minimum_cents': direct['total_projected_demurrage_cents'],
        'direct_solver_status': direct['solver_status'], 'direct_solver_seconds': direct['physical_solver_result']['cp_sat_seconds'],
        'direct_total_seconds': direct_time, 'direct_physical_validation': direct['physical_validation']['status'],
        'direct_inventory_validation': direct['inventory_validation'], 'direct_scope': direct['optimality_scope'],
        'old_minimum_cents': old['best_total_demurrage_cents'], 'old_evaluated_schedules': old['evaluated_schedule_count'],
        'old_solver_seconds': sum(run.get('physical_solver_result', {}).get('cp_sat_seconds', 0) for run in old.get('scheduling_runs', [])),
        'old_total_seconds': old_time, 'old_status': old['optimization_status'], 'old_scope': old['optimality_scope'],
        'old_physical_validation': old['best_candidate']['physical_validation']['status'],
        'old_inventory_validation': old['best_candidate']['inventory_validation'],
        'old_stopping_reason': old['stopping_reason'], 'simulation_only': True}))
