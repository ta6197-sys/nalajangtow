"""Repeated read-only A-search comparison on a saved source snapshot.

The source workbook labels its inputs SYNTHETIC; this is not live operations data.
"""
import argparse
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ortools.sat.python import cp_model
from weather_optimizer import WeatherSeries
from src.daily_planning import exact_search as search
from src.daily_planning.event_candidates import LEGACY, EVENTS_WEATHER
from src.daily_planning.evaluation import evaluate_baseline


def measure(snapshot, weather, variant, budget):
    started=time.perf_counter()
    domain=search.build_domain(snapshot,weather,candidate_generator=LEGACY if variant=='legacy' else EVENTS_WEATHER)
    domain_time=time.perf_counter()-started
    solver_calls=0; solver_seconds=0.; replay_calls=0; replay_seconds=0.; first=None
    real_solve, real_replay=cp_model.CpSolver.Solve,search.evaluate_baseline
    search_started=time.perf_counter()
    def observe(result):
        nonlocal first
        if first is None: first=time.perf_counter()-search_started
    class Cache(OrderedDict):
        def __getitem__(self,key):
            value=super().__getitem__(key);observe(value);return value
    cache=Cache() if variant in ('events_cache','events_hint') else None
    def solve(self,*args,**kwargs):
        nonlocal solver_calls,solver_seconds
        t=time.perf_counter()
        try: return real_solve(self,*args,**kwargs)
        finally: solver_calls+=1;solver_seconds+=time.perf_counter()-t
    def replay(*args,**kwargs):
        nonlocal replay_calls,replay_seconds
        t=time.perf_counter()
        try:
            result=real_replay(*args,**kwargs);observe(result);return result
        finally: replay_calls+=1;replay_seconds+=time.perf_counter()-t
    with patch.object(cp_model.CpSolver,'Solve',solve),patch.object(search,'evaluate_baseline',replay):
        result,record=search.cost_then_wait_search(snapshot,weather,domain,time_limit=budget,replay_cache=cache,warm_start=variant=='events_hint')
    elapsed=time.perf_counter()-search_started
    if result is None: raise RuntimeError('No feasible incumbent')
    plan=[r for r in result['evaluation']['vessel_results'] if r['assignment_kind']=='PLANNED']
    independent=evaluate_baseline(snapshot,plan,weather)
    assert independent['schedule_signature']==result['schedule_signature']
    assert independent['evaluation']['summary']==result['evaluation']['summary']
    return dict(variant=variant,budget=budget,options=len(domain['options']),domain_seconds=domain_time,
                search_seconds=elapsed,first_feasible_seconds=first,solver_seconds=solver_seconds,solver_calls=solver_calls,
                replay_seconds=replay_seconds,replay_calls=replay_calls,cost_cents=result['evaluation']['summary']['demurrage_increment_to_completion_cents'],
                signature=result['schedule_signature'],solver=record,independent_validation='PASS')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--snapshot',type=Path,required=True);ap.add_argument('--weather',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--budgets',nargs='+',type=float,default=[2,10]);ap.add_argument('--repeats',type=int,default=3)
    args=ap.parse_args();snapshot=json.loads(args.snapshot.read_text())
    if hashlib.sha256(args.weather.read_bytes()).hexdigest()!=snapshot['weather']['sha256']:raise ValueError('Weather fingerprint mismatch')
    weather=WeatherSeries.from_csv(args.weather);out=dict(schema='v2-phase1-repeated-v1',snapshot_hash=snapshot['snapshot_hash'],source_label='SYNTHETIC_SOURCE_WORKBOOK',results=[])
    variants=['legacy','events_only','events_cache','events_hint']
    for budget in args.budgets:
        for repeat in range(args.repeats):
            for offset in range(len(variants)):
                variant=variants[(repeat+offset)%len(variants)]
                result=measure(snapshot,weather,variant,budget);result['repeat']=repeat+1;out['results'].append(result)
                args.output.write_text(json.dumps(out,indent=2)+'\n')
                print('budget',budget,'repeat',repeat+1,variant,'options',result['options'],'cost',result['cost_cents'],'status',result['solver']['solver_status'],flush=True)
if __name__=='__main__':main()
