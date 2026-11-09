"""Isolated rollback test; the substituted selection timestamp is SYNTHETIC."""
import copy
from contextlib import contextmanager
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import sqlalchemy as sa
from src.daily_planning.database import get_engine
from src.daily_planning.models import planning_run
from src.daily_planning.forecast.database import forecast_for_run

class TransactionEngine:
    def __init__(self, connection): self.connection=connection
    @contextmanager
    def connect(self): yield self.connection


def main():
    if os.environ.get('POSTGRES_DB')!='docktimize_v2_phase0_isolated' or os.environ.get('DATABASE_URL'):
        raise ValueError('Only the isolated database is permitted')
    engine=get_engine()
    with engine.connect() as conn:
        tx=conn.begin()
        try:
            runs=conn.execute(sa.select(planning_run).order_by(planning_run.c.planning_as_of)).mappings().all()
            earlier, later=runs[0],runs[-1]
            original=copy.deepcopy(earlier['run_config'])
            actual=forecast_for_run(TransactionEngine(conn),later['run_id'])
            assert not actual['occupancy']
            synthetic=copy.deepcopy(original)
            synthetic['confirmation']['confirmed_at']='2026-11-03T12:34:56+09:00'
            conn.execute(planning_run.update().where(planning_run.c.run_id==earlier['run_id']).values(run_config=synthetic))
            result=forecast_for_run(TransactionEngine(conn),later['run_id'])
            assert len(result['occupancy'])==1 and result['occupancy'][0]['known_at']==synthetic['confirmation']['confirmed_at']
            print('actual late selection: excluded; synthetic known-at selection: included; read-only forecast: PASS')
        finally: tx.rollback()
        assert conn.execute(sa.select(planning_run.c.run_config).where(planning_run.c.run_id==earlier['run_id'])).scalar_one()==original
        print('rollback and original provenance: PASS')

if __name__=='__main__':main()
