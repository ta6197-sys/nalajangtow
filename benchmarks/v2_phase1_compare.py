"""Isolated synthetic comparison of finite-domain search variants."""
import argparse
from collections import OrderedDict
from decimal import Decimal
import json
from pathlib import Path
import platform
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ortools.sat.python import cp_model
from benchmarks.v2_phase0 import fixture
from src.daily_planning import exact_search as search
from src.daily_planning.event_candidates import LEGACY, EVENTS
from src.daily_planning.evaluation import evaluate_baseline


class ObservedCache(OrderedDict):
    observer = None
    def __getitem__(self, key):
        value = super().__getitem__(key)
        if self.observer is not None:
            self.observer(value)
        return value


def run(count, variant, budget):
    snapshot, weather = fixture(count)
    t = time.perf_counter()
    domain = search.build_domain(snapshot, weather, candidate_generator=LEGACY if variant == 'legacy' else EVENTS)
    domain_seconds = time.perf_counter() - t
    cache = ObservedCache() if variant in ('events_cache', 'events_hint') else None
    warm = variant == 'events_hint'
    candidates, records = [], []
    real_solve, real_replay = cp_model.CpSolver.Solve, search.evaluate_baseline
    for label in 'ABC':
        measurement = {'solver_seconds': 0.0, 'solver_calls': 0, 'replay_seconds': 0.0,
                       'replay_calls': 0, 'first_feasible_seconds': None}
        start = time.perf_counter()
        def solve(self, *args, **kwargs):
            before = time.perf_counter()
            try: return real_solve(self, *args, **kwargs)
            finally:
                measurement['solver_calls'] += 1
                measurement['solver_seconds'] += time.perf_counter() - before
        def observe(result):
            cost = result['evaluation']['summary']['demurrage_increment_to_completion_cents']
            if (measurement['first_feasible_seconds'] is None
                and result['schedule_signature'] not in {c['signature'] for c in candidates}
                and (label == 'A' or cost <= cap)):
                measurement['first_feasible_seconds'] = time.perf_counter() - start
        if cache is not None: cache.observer = observe
        def replay(*args, **kwargs):
            before = time.perf_counter()
            try:
                result = real_replay(*args, **kwargs)
                observe(result)
                return result
            finally:
                measurement['replay_calls'] += 1
                measurement['replay_seconds'] += time.perf_counter() - before
        with patch.object(cp_model.CpSolver, 'Solve', solve), patch.object(search, 'evaluate_baseline', replay):
            if label == 'A':
                result, record = search.cost_then_wait_search(snapshot, weather, domain, time_limit=budget, replay_cache=cache, warm_start=warm)
            else:
                result, record = search.exact_search(snapshot, weather, domain, objective='wait', time_limit=budget, cost_cap=cap, excluded_signatures=[c['signature'] for c in candidates], replay_cache=cache, warm_start=warm)
        elapsed = time.perf_counter() - start
        if result:
            cost = result['evaluation']['summary']['demurrage_increment_to_completion_cents']
            if label == 'A': cap = int(Decimal(cost) * Decimal('1.03'))
            planned = [r for r in result['evaluation']['vessel_results'] if r['assignment_kind'] == 'PLANNED']
            checked = evaluate_baseline(snapshot, planned, weather)
            assert checked['schedule_signature'] == result['schedule_signature']
            assert checked['evaluation']['summary'] == result['evaluation']['summary']
            candidates.append({'label': label, 'cost_cents': cost, 'signature': result['schedule_signature'], 'independent_replay': 'PASS'})
        elif label == 'A': raise RuntimeError('No A candidate')
        records.append({'label': label, 'seconds': elapsed, 'measurements': measurement, 'search': record})
    return {'calls': count, 'variant': variant, 'domain_seconds': domain_seconds, 'options': len(domain['options']), 'candidates': candidates, 'records': records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--budget', type=float, default=1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = {'schema': 'phase1-synthetic-v2', 'python': platform.python_version(), 'budget_seconds_per_candidate': args.budget,
              'timing_note': 'first_feasible_seconds observes accepted replay or cache retrieval; it includes validation and precedes final candidate selection',
              'results': [run(n, v, args.budget) for n in (2, 4, 8) for v in ('legacy', 'events_only', 'events_cache', 'events_hint')]}
    args.output.write_text(json.dumps(report, indent=2) + '\n')

if __name__ == '__main__': main()
