"""Transactional optimize_run persistence check on an isolated PostgreSQL copy."""
from contextlib import contextmanager
import copy
import os
from pathlib import Path
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import sqlalchemy as sa
from src.daily_planning.context import signature
from src.daily_planning.database import get_engine
from src.daily_planning.event_candidates import EVENTS_WEATHER
from src.daily_planning.models import planning_run, schedule_candidate
from src.daily_planning.optimization import optimize_run


class TransactionEngine:
    def __init__(self, connection): self.connection=connection
    @contextmanager
    def connect(self): yield self.connection
    @contextmanager
    def begin(self): yield self.connection


def main():
    if os.environ.get('POSTGRES_DB')!='docktimize_v2_phase0_isolated' or os.environ.get('DATABASE_URL'):
        raise ValueError('Only the isolated database is permitted')
    engine=get_engine(); new_id=uuid.uuid4()
    with tempfile.TemporaryDirectory(prefix='docktimize-v2-phase1-') as folder, engine.connect() as conn:
        tx=conn.begin()
        try:
            source=conn.execute(sa.select(planning_run).order_by(planning_run.c.planning_as_of.desc())).mappings().first()
            data=dict(source); config=copy.deepcopy(data['run_config']);config.pop('optimization',None);config.pop('confirmation',None);config.pop('operation',None)
            snapshot=config['input_snapshot'];weather=ROOT/'input/weather_for_optimizer_30min.csv'
            snapshot['weather']['path']=str(weather);snapshot['daylight_windows']['path']=str(ROOT/'src/daily_planning/daylight_windows.csv')
            snapshot['snapshot_hash']=signature({k:v for k,v in snapshot.items() if k!='snapshot_hash'})
            data.update(run_id=new_id,run_config=config,solver_status='INPUT_READY',weather_file_path=str(weather),input_snapshot_path=str(Path(folder)/'input_snapshot.json'))
            conn.execute(planning_run.insert().values(**data))
            result=optimize_run(TransactionEngine(conn),new_id,time_limit=2,candidate_generator=EVENTS_WEATHER,warm_start=True)
            stored=conn.execute(sa.select(planning_run.c.run_config).where(planning_run.c.run_id==new_id)).scalar_one()['optimization']
            count=conn.scalar(sa.select(sa.func.count()).select_from(schedule_candidate).where(schedule_candidate.c.run_id==new_id))
            assert stored['candidate_generator']==EVENTS_WEATHER and stored['warm_start'] is True
            assert count==result['candidate_count'] and count>0
            assert optimize_run(TransactionEngine(conn),new_id,time_limit=2,candidate_generator=EVENTS_WEATHER,warm_start=True)['candidate_count']==count
            try: optimize_run(TransactionEngine(conn),new_id,time_limit=2,candidate_generator=EVENTS_WEATHER,warm_start=False)
            except ValueError: pass
            else: raise AssertionError('Changed policy accepted on retry')
            print('persisted candidates:',count,'policy:',stored['candidate_generator'],'warm_start:',stored['warm_start'],'idempotent retry: PASS','different policy retry: REJECTED')
        finally: tx.rollback()
        assert conn.scalar(sa.select(sa.func.count()).select_from(planning_run).where(planning_run.c.run_id==new_id))==0
        print('rollback: PASS')

if __name__=='__main__':main()
