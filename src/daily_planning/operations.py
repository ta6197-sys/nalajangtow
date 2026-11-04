"""User selection, 24h freeze, and transactional virtual operation advance."""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import sqlalchemy as sa

from weather_optimizer import WeatherSeries

from .bootstrap_db import KST, kst_time
from .context import (experiment_lock, local_time, plain, read_snapshot, signature,
                      verify_snapshot)
from .evaluation import evaluate_baseline
from .execution import execute_committed_window
from .models import (call_state, planning_run, schedule_assignment,
                     schedule_candidate, schedule_commit)
from .optimization import write_json


def _locked_run(connection, run_id):
    row=connection.execute(sa.select(planning_run).where(planning_run.c.run_id==run_id)).mappings().one()
    experiment_lock(connection,row['experiment_key'])
    return connection.execute(sa.select(planning_run).where(planning_run.c.run_id==run_id)
                              .with_for_update()).mappings().one()


def _check_current(connection,run):
    frozen=run['run_config']['input_snapshot']
    verify_snapshot(frozen)
    fresh=read_snapshot(connection,run['experiment_key'],run['planning_as_of'],run['weather_file_path'],
        berthing_policy=frozen.get('berthing_policy', 'LEGACY_UNRESTRICTED'))
    if 'berthing_policy' not in frozen:
        fresh.pop('berthing_policy', None)
        fresh.pop('daylight_windows', None)
        fresh['snapshot_hash']=signature({k:v for k,v in fresh.items() if k!='snapshot_hash'})
    # Early input-only runs predate this optional reference field.
    if 'previous_tentative_assignments' not in frozen and not fresh.get('previous_tentative_assignments'):
        fresh.pop('previous_tentative_assignments',None)
        fresh['snapshot_hash']=signature({k:v for k,v in fresh.items() if k!='snapshot_hash'})
    if fresh!=frozen:
        raise ValueError('Stale candidate: operating inputs or commitments changed; replan')
    return frozen


def confirm_in_transaction(connection,candidate_id):
    candidate_id=uuid.UUID(str(candidate_id))
    candidate=connection.execute(sa.select(schedule_candidate)
                                 .where(schedule_candidate.c.candidate_id==candidate_id)).mappings().one()
    run=_locked_run(connection,candidate['run_id'])
    config=copy.deepcopy(run['run_config'])
    previous=config.get('confirmation')
    if previous:
        if previous['candidate_id']!=str(candidate_id):
            raise ValueError('A different candidate is already confirmed for this run')
        return previous
    for other in connection.execute(sa.select(planning_run.c.run_config).where(
            planning_run.c.experiment_key==run['experiment_key'],
            planning_run.c.planning_as_of==run['planning_as_of'],planning_run.c.run_id!=run['run_id'])).scalars():
        if other.get('confirmation'):
            raise ValueError('Another run is already confirmed for this operating time')
    snapshot=_check_current(connection,run)
    rows=[dict(r) for r in connection.execute(sa.select(schedule_assignment)
          .where(schedule_assignment.c.candidate_id==candidate_id).order_by(schedule_assignment.c.call_id)).mappings()]
    existing_ids={r['call_id'] for r in snapshot['commits']}
    planned=[r for r in rows if r['call_id'] not in existing_ids]
    replay=evaluate_baseline(snapshot,planned,WeatherSeries.from_csv(Path(run['weather_file_path'])))
    metrics=candidate['evaluation_metrics']
    if (replay['schedule_signature']!=metrics['schedule_signature']
            or replay['evaluation']['summary']['demurrage_increment_to_completion_cents']!=candidate['simulated_demurrage_cents']):
        raise ValueError('Stored candidate does not match its immutable BASELINE schedule')
    now=datetime.now(KST)
    added=[]
    for row in rows:
        if row['call_id'] in existing_ids:
            continue
        if run['planning_as_of']<=row['start_at']<run['freeze_end_at']:
            commit_id=uuid.uuid5(candidate_id,'commit:'+row['call_id'])
            connection.execute(schedule_commit.insert().values(
                commit_id=commit_id,assignment_id=row['assignment_id'],experiment_key=run['experiment_key'],
                berth_id=row['berth_id'],confirmed_at=now,locked_from_at=row['start_at'],
                locked_until_at=row['completion_at'],status='LOCKED'))
            added.append(str(commit_id))
    confirmation=plain(dict(confirmation_id=uuid.uuid5(candidate_id,'confirmation'),candidate_id=candidate_id,
        confirmed_at=now,status='CONFIRMED',selection_policy='USER_CANDIDATE_CHOICE',
        freeze_start_at=run['planning_as_of'],freeze_end_at=run['freeze_end_at'],
        new_commit_ids=added,retained_commit_ids=[c['commit_id'] for c in snapshot['commits']],
        input_snapshot_hash=snapshot['snapshot_hash']))
    config['confirmation']=confirmation
    connection.execute(planning_run.update().where(planning_run.c.run_id==run['run_id']).values(run_config=config))
    return confirmation


def advance_in_transaction(connection,run_id):
    run_id=uuid.UUID(str(run_id))
    run=_locked_run(connection,run_id)
    config=copy.deepcopy(run['run_config'])
    if 'operation' in config:
        return config['operation']
    if 'confirmation' not in config:
        raise ValueError('Select and confirm a candidate before advancing operation')
    frozen=config['input_snapshot']
    verify_snapshot(frozen)
    # Fresh commitments now include the user's selection. The source state,
    # contracts, calls and weather must still be exactly the frozen inputs.
    operation_input=read_snapshot(connection,run['experiment_key'],run['planning_as_of'],run['weather_file_path'],
        berthing_policy=frozen.get('berthing_policy', 'LEGACY_UNRESTRICTED'))
    for key in ('calls','contracts','berths','call_states','source_state_ids','weather'):
        if operation_input[key]!=frozen[key]:
            raise ValueError('Operating state changed after confirmation; operation must not overwrite it')
    end=run['freeze_end_at']
    if end-run['planning_as_of']!=timedelta(hours=24):
        raise ValueError('Operation step must be exactly 24h')
    result=execute_committed_window(operation_input,end,WeatherSeries.from_csv(Path(run['weather_file_path'])))
    prior={r['call_id']:dict(r) for r in connection.execute(sa.select(call_state)
           .where(call_state.c.experiment_key==run['experiment_key'],call_state.c.state_at==run['planning_as_of'])).mappings()}
    next_states=[]
    for row in result['vessel_results']:
        started=row['work_start_at'] is not None and local_time(row['work_start_at'])<local_time(end)
        completed=row['completion_at'] is not None and local_time(row['completion_at'])<=local_time(end)
        source=next(c for c in operation_input['calls'] if c['call_id']==row['call_id'])
        status=('COMPLETED' if completed else 'IN_PROGRESS' if started else
                'WAITING' if kst_time(source['available_at'])<=end else 'PLANNED')
        clock=dict(row['contract_clock_at_24h'],remaining_work_minutes=row['remaining_work_minutes_at_24h'],
                   accrued_despatch_cents=row['despatch_to_completion_cents'] if completed else 0,
                   source_run_id=str(run_id),operation_input_hash=operation_input['snapshot_hash'])
        if started:
            clock['schedule']={k:row[k] for k in ('work_start_at','completion_at','berth_id','weather_pause_intervals','work_intervals','work_pause_intervals','night_pause_intervals')}
        next_states.append(dict(state_id=uuid.uuid5(run_id,'state:'+row['call_id']),call_id=row['call_id'],
            berth_id=row['berth_id'] if status=='IN_PROGRESS' else None,experiment_key=run['experiment_key'],state_at=end,
            status=status,remaining_work_kg=row['remaining_work_kg_at_24h'],
            accrued_demurrage_cents=row['accrued_before_cents']+row['demurrage_increment_24h_cents'],
            contract_clock_state=clock))
    for cid,old in prior.items():
        if old['status'] in ('COMPLETED','CANCELLED'):
            next_states.append(dict(old,state_id=uuid.uuid5(run_id,'state:'+cid),state_at=end))
    for state in next_states:
        connection.execute(call_state.insert().values(**state))
    connection.execute(schedule_commit.update().where(
        schedule_commit.c.experiment_key==run['experiment_key'],schedule_commit.c.locked_until_at<=end)
        .values(status='COMPLETED'))
    connection.execute(schedule_commit.update().where(
        schedule_commit.c.experiment_key==run['experiment_key'],schedule_commit.c.locked_from_at<end,
        schedule_commit.c.locked_until_at>end).values(status='IN_PROGRESS'))
    operation=plain(dict(step_id=uuid.uuid5(run_id,'advance24'),status='SUCCEEDED',from_at=run['planning_as_of'],to_at=end,
        input_snapshot_hash=frozen['snapshot_hash'],operation_input_hash=operation_input['snapshot_hash'],
        applied_commit_ids=[c['commit_id'] for c in operation_input['commits']],
        actual_day_demurrage_cents=result['summary']['demurrage_increment_24h_cents'],
        actual_day_despatch_cents=sum(r['despatch_to_completion_cents'] for r in result['vessel_results']),
        accumulated_demurrage_cents=sum(s['accrued_demurrage_cents'] for s in next_states),
        completed_call_ids=[s['call_id'] for s in next_states if s['status']=='COMPLETED'],
        ongoing_call_ids=[s['call_id'] for s in next_states if s['status']=='IN_PROGRESS'],
        next_states=next_states))
    config['operation']=operation
    connection.execute(planning_run.update().where(planning_run.c.run_id==run_id).values(run_config=config))
    return operation


def confirm_candidate(engine,candidate_id):
    with engine.begin() as connection:
        result=confirm_in_transaction(connection,candidate_id)
        run=connection.execute(sa.select(planning_run)
            .join(schedule_candidate,planning_run.c.run_id==schedule_candidate.c.run_id)
            .where(schedule_candidate.c.candidate_id==uuid.UUID(str(candidate_id)))).mappings().one()
        folder=Path(run['input_snapshot_path']).parent
    write_json(folder/'confirmation.json',result)
    return result


def advance_run(engine,run_id):
    with engine.begin() as connection:
        result=advance_in_transaction(connection,run_id)
        run=connection.execute(sa.select(planning_run).where(planning_run.c.run_id==uuid.UUID(str(run_id)))).mappings().one()
        folder=Path(run['input_snapshot_path']).parent
    write_json(folder/'operation_step.json',result)
    write_json(folder/'next_day_state.json',dict(state_at=result['to_at'],states=result['next_states']))
    return result
