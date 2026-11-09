"""Read-only search check against the explicitly isolated PostgreSQL copy."""
from collections import OrderedDict
from decimal import Decimal
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import sqlalchemy as sa
from weather_optimizer import WeatherSeries
from src.daily_planning.database import get_engine
from src.daily_planning.evaluation import evaluate_baseline
from src.daily_planning.event_candidates import EVENTS
from src.daily_planning.exact_search import build_domain, cost_then_wait_search, exact_search
from src.daily_planning.models import planning_run


def main():
    if os.environ.get('POSTGRES_DB') != 'docktimize_v2_phase0_isolated' or os.environ.get('DATABASE_URL'):
        raise ValueError('Only the explicitly isolated database may be used')
    engine = get_engine()
    with engine.connect() as connection:
        connection.execute(sa.text('SET TRANSACTION READ ONLY'))
        snapshot = connection.execute(sa.select(planning_run.c.run_config).order_by(planning_run.c.planning_as_of.desc())).scalars().first()['input_snapshot']
        weather_path = ROOT / 'input/weather_for_optimizer_30min.csv'
        import hashlib
        if hashlib.sha256(weather_path.read_bytes()).hexdigest() != snapshot['weather']['sha256']:
            raise AssertionError('Weather input differs from frozen snapshot')
        weather = WeatherSeries.from_csv(weather_path)
        domain = build_domain(snapshot, weather, candidate_generator=EVENTS)
        cache = OrderedDict()
        candidates = []
        for label in 'ABC':
            if label == 'A':
                result, record = cost_then_wait_search(snapshot, weather, domain, time_limit=2, replay_cache=cache, warm_start=True)
            else:
                objective = 'stability' if label == 'C' and snapshot.get('previous_tentative_assignments') else 'wait'
                result, record = exact_search(snapshot, weather, domain, objective=objective, time_limit=2,
                    cost_cap=cap, excluded_signatures=[c['signature'] for c in candidates], replay_cache=cache, warm_start=True)
            if result is None:
                print(label, 'no candidate; status:', record['solver_status'])
                if label == 'A': raise AssertionError('No feasible A result')
                continue
            cost = result['evaluation']['summary']['demurrage_increment_to_completion_cents']
            if label == 'A': cap = int(Decimal(cost) * Decimal('1.03'))
            planned = [r for r in result['evaluation']['vessel_results'] if r['assignment_kind'] == 'PLANNED']
            replay = evaluate_baseline(snapshot, planned, weather)
            assert result['schedule_signature'] == replay['schedule_signature']
            # Stability search attaches comparison-only summary fields.
            for key, value in replay['evaluation']['summary'].items():
                assert result['evaluation']['summary'][key] == value
            candidates.append({'signature': result['schedule_signature'], 'cost': cost})
            print(label, 'calls:', len(snapshot['calls']), 'options:', len(domain['options']),
                  'status:', record['solver_status'], 'cost_cents:', cost,
                  'signature:', result['schedule_signature'], 'independent_replay: PASS')
        connection.rollback()

if __name__ == '__main__': main()
