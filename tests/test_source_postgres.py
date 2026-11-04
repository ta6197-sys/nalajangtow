"""Opt-in transactional integration test against the fresh source-data database.

RUN_SOURCE_DB_TESTS=1 python -m unittest discover -s tests -p test_source_postgres.py -v
All confirmation and operation writes are rolled back, including after failure.
"""
import os
import unittest
import uuid
from datetime import timedelta

import sqlalchemy as sa
from weather_optimizer import WeatherSeries
from src.daily_planning.database import get_engine
from src.daily_planning.models import planning_run, schedule_candidate, call_state, port_call, contract
from src.daily_planning.context import read_snapshot, local_time
from src.daily_planning.candidate_store import save_evaluated_candidate
from src.daily_planning.daylight import daylight_window
from src.daily_planning.evaluation import evaluate_baseline
from src.daily_planning.exact_search import build_domain
from src.daily_planning.operations import confirm_in_transaction, advance_in_transaction


@unittest.skipUnless(os.getenv('RUN_SOURCE_DB_TESTS')=='1','Requires explicit source-DB integration opt-in')
class SourcePostgresTest(unittest.TestCase):
    def test_three_real_operating_steps_rollback(self):
        engine=get_engine()
        self.assertEqual(engine.url.database,'ulsan_clx_source_daywork_v2')
        with engine.connect() as connection:
            tx=connection.begin()
            try:
                self.assertEqual(connection.scalar(sa.select(sa.func.count()).select_from(contract)),144)
                self.assertEqual(connection.scalar(sa.select(sa.func.count()).select_from(port_call)),140)
                if connection.scalar(sa.select(sa.func.count()).select_from(call_state)):
                    self.skipTest('Live source scenario has already advanced; do not alter it')
                run=dict(connection.execute(sa.select(planning_run).where(planning_run.c.experiment_key=='source-daywork-v2')).mappings().one())
                candidate=connection.scalar(sa.select(schedule_candidate.c.candidate_id).where(schedule_candidate.c.run_id==run['run_id'],schedule_candidate.c.candidate_rank==1))
                total_work_segments=0
                for day in range(3):
                    confirm_in_transaction(connection,candidate)
                    step=advance_in_transaction(connection,run['run_id'])
                    for row in step['next_states']:
                        for a,b in row['contract_clock_state'].get('schedule',{}).get('work_intervals',[]):
                            a,b=local_time(a),local_time(b);rise,setting=daylight_window(a)
                            self.assertTrue(rise<=a<b<=setting)
                            total_work_segments+=1
                    as_of=run['freeze_end_at']
                    snap=read_snapshot(connection,'source-daywork-v2',as_of,run['weather_file_path'])
                    weather=WeatherSeries.from_csv(__import__('pathlib').Path(run['weather_file_path']))
                    domain=build_domain(snap,weather)
                    selected=[domain['options'][j]['row'] for j in domain['seeds'][0] if domain['options'][j]['row'] is not None]
                    result=evaluate_baseline(snap,selected,weather)
                    if day==2:break
                    run=dict(run_id=uuid.uuid4(),experiment_key='source-daywork-v2',planning_as_of=as_of,
                        horizon_end_at=as_of+timedelta(hours=72),freeze_end_at=as_of+timedelta(hours=24),
                        input_snapshot_path='/tmp/transactional-test-no-export.json',weather_file_path=run['weather_file_path'],
                        run_config={'input_snapshot':snap},solver_status='FEASIBLE')
                    connection.execute(planning_run.insert().values(**run))
                    candidate=uuid.uuid4()
                    save_evaluated_candidate(connection,run['run_id'],candidate_id=candidate,
                        strategy='TRANSACTIONAL_TEST_ONLY',candidate_rank=1,
                        expected_demurrage_cents=result['evaluation']['summary']['demurrage_increment_to_completion_cents'],
                        evaluation_result=result,solver_details={'solver_status':'FEASIBLE','relative_gap':None})
                self.assertGreater(total_work_segments,0)
            finally:
                tx.rollback()
        with engine.connect() as connection:
            self.assertEqual(connection.scalar(sa.select(sa.func.count()).select_from(call_state)),0)
            configs=connection.execute(sa.select(planning_run.c.run_config)).scalars()
            self.assertTrue(all('confirmation' not in config for config in configs))
