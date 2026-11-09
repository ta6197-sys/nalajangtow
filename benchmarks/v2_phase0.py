#!/usr/bin/env python3
"""Isolated synthetic benchmark; no database access or production mutations."""
import argparse
import copy
import json
import platform
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ortools.sat.python import cp_model
from src.daily_planning import exact_search as search
from src.daily_planning.context import signature
import weather_optimizer
import rolling_horizon
from src.daily_planning import evaluation
from weather_optimizer import WeatherSeries


def fixture(n):
    """Synthetic calls and contracts; quantities and rates are not operational data."""
    now = datetime(2026, 11, 1)
    terms = dict(notice_hours=0, early_start_if_work='N', nor_valid='Y', day_mode='SHINC',
                 weather_policy='EXCLUDE', post_demurrage_exceptions='NONE', despatch_policy='NONE', holidays=[])
    snap = dict(experiment_key='SYNTHETIC_PHASE0', as_of_at=now.isoformat(),
                contracts=[], calls=[], call_states=[], commits=[],
                berths=[dict(berth_id=f'B{i}', is_active=True) for i in range(2)],
                previous_tentative_assignments=[])
    for i in range(n):
        cid = f'SYN-{i:03d}'
        arrival = (now + timedelta(hours=i * 2)).isoformat()
        minutes = 120 + (i % 3) * 30
        snap['contracts'].append(dict(contract_id=cid, allowed_minutes=60, demurrage_daily_cents=144000 + i*1000, contract_terms=copy.deepcopy(terms)))
        snap['calls'].append(dict(call_id=cid, contract_id=cid, nor_at=arrival, available_at=arrival, work_minutes=minutes, cargo_quantity_kg=minutes*1000))
        snap['call_states'].append(dict(call_id=cid, status='WAITING', remaining_work_kg=minutes*1000, accrued_demurrage_cents=0, contract_clock_state={'remaining_work_minutes':minutes}))
    snap['snapshot_hash'] = signature(snap)
    rows = [dict(일시=(now + timedelta(minutes=30*i)).isoformat(), **{'풍속(m/s)':0,'유의파고(m)':0,'최대파고(m)':0,'평균파고(m)':0}) for i in range(48*30)]
    return snap, WeatherSeries(rows)


def run(n, budget):
    total = time.perf_counter()
    t = time.perf_counter(); snapshot, weather = fixture(n); preparation = time.perf_counter()-t
    t = time.perf_counter(); domain = search.build_domain(snapshot, weather); domain_time = time.perf_counter()-t
    durations = {'solver_seconds':0., 'replay_seconds':0., 'solver_calls':0, 'replay_calls':0, 'pricing_seconds':0., 'pricing_calls':0}
    real_solve, real_replay = cp_model.CpSolver.Solve, search.evaluate_baseline
    def solve(self, *args, **kwargs):
        start = time.perf_counter()
        try: return real_solve(self, *args, **kwargs)
        finally:
            durations['solver_calls'] += 1; durations['solver_seconds'] += time.perf_counter()-start
    def replay(*args, **kwargs):
        start = time.perf_counter()
        try: return real_replay(*args, **kwargs)
        finally:
            durations['replay_calls'] += 1; durations['replay_seconds'] += time.perf_counter()-start
    real_price = weather_optimizer.price_contract
    def price(*args, **kwargs):
        start = time.perf_counter()
        try: return real_price(*args, **kwargs)
        finally:
            durations['pricing_calls'] += 1; durations['pricing_seconds'] += time.perf_counter()-start
    records=[]; candidates=[]
    with patch.object(weather_optimizer, 'price_contract', price), patch.object(rolling_horizon, 'price_contract', price), patch.object(evaluation, 'price_contract', price), patch.object(cp_model.CpSolver, 'Solve', solve), patch.object(search, 'evaluate_baseline', replay):
        for label in 'ABC':
            t=time.perf_counter()
            if label=='A': result, record = search.cost_then_wait_search(snapshot, weather, domain, time_limit=budget)
            else:
                cap=int(cost_a*1.03)
                result, record=search.exact_search(snapshot,weather,domain,objective='wait',time_limit=budget,cost_cap=cap,excluded_signatures=[x['signature'] for x in candidates])
            elapsed=time.perf_counter()-t
            if result is not None:
                summary=result['evaluation']['summary']; cost=summary['demurrage_increment_to_completion_cents']
                if label=='A': cost_a=cost
                candidates.append(dict(label=label,cost_cents=cost,signature=result['schedule_signature'],deferred_calls=sum(r['assignment_kind']=='EVALUATION_TAIL' for r in result['evaluation']['vessel_results'])))
            elif label=='A': raise RuntimeError('No baseline candidate')
            records.append(dict(label=label,wall_seconds=elapsed,solver=record))
    return dict(fixture='SYNTHETIC',calls=n,berths=2,preparation_seconds=preparation,domain_seconds=domain_time,options=len(domain['options']),physical_options=sum(o['row'] is not None for o in domain['options']),timing=durations,candidates=candidates,searches=records,total_seconds=time.perf_counter()-total)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--budget',type=float,default=2); parser.add_argument('--output',type=Path,required=True); args=parser.parse_args()
    output=dict(schema='phase0-synthetic-v1',python=platform.python_version(),budget_seconds_per_candidate=args.budget,results=[run(n,args.budget) for n in (2,4,8)])
    args.output.write_text(json.dumps(output,indent=2)+'\n')

if __name__=='__main__': main()
