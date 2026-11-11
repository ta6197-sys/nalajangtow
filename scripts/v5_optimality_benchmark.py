"""Reproduce tiny synthetic candidate-cap measurements, not production timings."""
import copy
import json
from unittest.mock import patch
from tests.test_v5_cost_aware import make_case
from tests.test_v5_physical_integration import START
from src.daily_planning.integrated_v3.v5_physical import schedule_v5

for n in (3,5):
    s,p,t=make_case(n)
    for c,m in zip(s['calls'],(0,30,60) if n==3 else (540,570,600,630,660)):
        c['eta_at']=f'2026-11-03T{m//60:02}:{m%60:02}:00+09:00'
    for tid in ('U','V'):
        tank=copy.deepcopy(s['tanks'][0]);tank.update(tank_id=tid,cargo_group=tid,connected_berth_ids=[]);s['tanks'].append(tank)
        flow=copy.deepcopy(s['fixed_process_flows']['segments'][0]);flow['tank_id']=tid;s['fixed_process_flows']['segments'].append(flow)
    for cap in (6,12,24):
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',return_value=copy.deepcopy(s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}),patch('src.daily_planning.integrated_v3.v5_physical.prepare_pilotage_policy',side_effect=lambda s,c,e,p,*a:(p,None)):
            r=schedule_v5(s,data_dir='SYNTHETIC',call_ids=list(t),as_of_at=START,policy=p,candidate_generation_mode='AUTO_ADAPTIVE_CANDIDATES',objective_mode='MIN_TOTAL_DEMURRAGE',inventory_constraint_mode='EXACT_EVENT_BALANCE_V1',pricing_policy='CLEAR_WEATHER_BASELINE_V1',time_limit_seconds=15,max_candidates_per_pair=cap,candidate_improvement_mode='COST_AWARE_V1')
        print(json.dumps({'vessels':n,'cap':cap,**{k:r.get(k) for k in ('status','initial_candidate_count','final_candidate_count','initial_total_demurrage_cents','best_total_demurrage_cents','runtime_seconds','solver_attempt_count','final_domain_optimality_proven','stopping_reason')},'final_options':r.get('final_solver_result',{}).get('option_count')}))
