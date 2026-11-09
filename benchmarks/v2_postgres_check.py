"""Opt-in rollback check; use only the explicitly named isolated copy."""
import copy, os, sys, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from pathlib import Path
import sqlalchemy as sa
from src.daily_planning.database import get_engine
from src.daily_planning.models import planning_run, schedule_candidate, call_state, operation_event
from src.daily_planning.context import signature, read_snapshot
from src.daily_planning.operations import confirm_in_transaction, advance_in_transaction
assert os.environ.get('POSTGRES_DB') == 'docktimize_v2_phase0_isolated', 'Set POSTGRES_DB to the isolated copy'
assert not os.environ.get('DATABASE_URL'), 'DATABASE_URL must not override the isolated copy'
engine=get_engine()
with engine.connect() as c:
    tx=c.begin()
    try:
        run=c.execute(sa.select(planning_run).order_by(planning_run.c.planning_as_of.desc())).mappings().first()
        config=copy.deepcopy(run['run_config']); path=str(Path('input/weather_for_optimizer_30min.csv').resolve())
        config['input_snapshot']['weather']['path']=path
        snapshot=config['input_snapshot'];snapshot['daylight_windows']['path']=str(Path('src/daily_planning/daylight_windows.csv').resolve());snapshot['snapshot_hash']=signature({k:v for k,v in snapshot.items() if k!='snapshot_hash'})
        c.execute(planning_run.update().where(planning_run.c.run_id==run['run_id']).values(weather_file_path=path,run_config=config))
        started=time.perf_counter()
        observed=read_snapshot(c,run['experiment_key'],run['planning_as_of'],path,berthing_policy=snapshot['berthing_policy'])
        elapsed=time.perf_counter()-started
        assert observed == snapshot
        print('snapshot preparation seconds:', round(elapsed, 6))
        candidate=c.execute(sa.select(schedule_candidate.c.candidate_id).where(schedule_candidate.c.run_id==run['run_id']).order_by(schedule_candidate.c.candidate_rank)).scalar()
        confirmation=confirm_in_transaction(c,candidate)
        result=advance_in_transaction(c,run['run_id'])
        print('confirmation:', confirmation['status'], 'advance:',result['status'],'next_states:',len(result['next_states']))
        assert advance_in_transaction(c,run['run_id']) == result
        print('idempotent: True')
    finally: tx.rollback()
