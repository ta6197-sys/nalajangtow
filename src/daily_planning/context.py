"""Frozen PostgreSQL inputs for one daily planning problem; no state mutation."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa

from .bootstrap_db import KST, kst_time
from .input_loader import fingerprint
from .models import (berth, call_state, contract, operation_event, planning_run, port_call,
                     schedule_assignment, schedule_candidate, schedule_commit)

ROOT = Path(__file__).resolve().parents[2]


def json_value(value):
    if isinstance(value, datetime):
        return kst_time(value).isoformat()
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    raise TypeError(f'Unsupported snapshot type: {type(value)}')


def canonical(value):
    return json.dumps(value, default=json_value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def plain(value):
    return json.loads(canonical(value))


def signature(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def local_time(value):
    """The legacy weather/contract engine uses naive KST minutes."""
    at = kst_time(value)
    if at.second or at.microsecond:
        raise ValueError('Planning times must resolve to whole minutes')
    return at.replace(tzinfo=None)


def experiment_lock(connection, experiment_key):
    if not experiment_key or not experiment_key.strip():
        raise ValueError('experiment_key is required')
    connection.execute(sa.text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                       {'key': experiment_key})


def previous_tentative(connection, experiment_key, as_of, active_ids, committed_ids):
    """Use only a previously selected candidate, never an arbitrary prior A."""
    runs=connection.execute(sa.select(planning_run)
        .where(planning_run.c.experiment_key==experiment_key,planning_run.c.planning_as_of<as_of)
        .order_by(planning_run.c.planning_as_of.desc(),planning_run.c.run_id)).mappings()
    for run in runs:
        chosen=run['run_config'].get('confirmation',{}).get('candidate_id')
        if chosen:
            selected=[uuid.UUID(str(chosen))]
        else:
            selected=list(connection.execute(sa.select(schedule_assignment.c.candidate_id).distinct()
                .join(schedule_commit,schedule_commit.c.assignment_id==schedule_assignment.c.assignment_id)
                .join(schedule_candidate,schedule_candidate.c.candidate_id==schedule_assignment.c.candidate_id)
                .where(schedule_candidate.c.run_id==run['run_id'],schedule_commit.c.experiment_key==experiment_key)).scalars())
        if not selected:
            continue
        if len(selected)!=1:
            raise ValueError('Previous planning run has multiple selected candidates')
        source=connection.execute(sa.select(schedule_candidate.c.run_id)
                                  .where(schedule_candidate.c.candidate_id==selected[0])).scalar_one()
        if source!=run['run_id']:
            raise ValueError('Previous selected candidate belongs to another run')
        rows=[dict(r) for r in connection.execute(sa.select(schedule_assignment)
            .where(schedule_assignment.c.candidate_id==selected[0],schedule_assignment.c.start_at>=as_of,
                   schedule_assignment.c.call_id.in_(active_ids-committed_ids))
            .order_by(schedule_assignment.c.call_id)).mappings()]
        return rows
    return []


def read_snapshot(connection, experiment_key, as_of, weather_path, *, berthing_policy='DAYLIGHT_WORK_V2'):
    as_of = kst_time(as_of)
    local_time(as_of)
    cancelled_ids = set(connection.execute(sa.select(operation_event.c.call_id).where(
        operation_event.c.experiment_key == experiment_key,
        operation_event.c.event_kind == 'CALL_CANCELLED',
        operation_event.c.event_at <= as_of)).scalars())
    calls = [dict(r) for r in connection.execute(sa.select(port_call)
             .where(port_call.c.known_at <= as_of,
                    ~port_call.c.call_id.in_(cancelled_ids)).order_by(port_call.c.call_id)).mappings()]
    latest_at = connection.scalar(sa.select(sa.func.max(call_state.c.state_at))
                                 .where(call_state.c.experiment_key == experiment_key))
    first_at = connection.scalar(sa.select(sa.func.min(port_call.c.known_at)).where(
        ~port_call.c.call_id.in_(cancelled_ids)))
    if latest_at is None and first_at is not None:
        initial = kst_time(first_at).replace(hour=0, minute=0, second=0, microsecond=0)
        if first_at > initial:
            initial += timedelta(days=1)
        if as_of != initial:
            raise ValueError('A new experiment must start at the first midnight after disclosure; import an actual state to start later')
    if latest_at is not None and latest_at != as_of:
        raise ValueError('Planning as_of must match the latest operating state; advance operation first')
    stored_states = [dict(r) for r in connection.execute(sa.select(call_state)
                     .where(call_state.c.experiment_key == experiment_key, call_state.c.state_at == as_of)
                     .order_by(call_state.c.call_id)).mappings()]
    by_state = {s['call_id']: s for s in stored_states}
    if latest_at is not None:
        for c in calls:
            if c['known_at'] < as_of and c['call_id'] not in by_state:
                raise ValueError(f'Missing carried state for {c["call_id"]}')
    active = [c for c in calls if by_state.get(c['call_id'], {}).get('status') not in ('COMPLETED', 'CANCELLED')]
    ids = {c['call_id'] for c in active}
    contracts = [dict(r) for r in connection.execute(sa.select(contract)
                 .where(contract.c.contract_id.in_({c['contract_id'] for c in active}))
                 .order_by(contract.c.contract_id)).mappings()]
    berths = [dict(r) for r in connection.execute(sa.select(berth).order_by(berth.c.berth_id)).mappings()]
    if not any(b['is_active'] for b in berths):
        raise ValueError('No active berth')
    commits = []
    query = (sa.select(schedule_commit, schedule_assignment.c.call_id,
                       schedule_assignment.c.berth_id.label('assignment_berth_id'),
                       schedule_assignment.c.start_at, schedule_assignment.c.completion_at,
                       planning_run.c.experiment_key.label('run_experiment_key'))
             .join(schedule_assignment, schedule_commit.c.assignment_id == schedule_assignment.c.assignment_id)
             .join(schedule_candidate, schedule_assignment.c.candidate_id == schedule_candidate.c.candidate_id)
             .join(planning_run, schedule_candidate.c.run_id == planning_run.c.run_id)
             .where(schedule_commit.c.experiment_key == experiment_key,
                    schedule_commit.c.status.in_(('LOCKED', 'IN_PROGRESS', 'COMPLETED')),
                    schedule_commit.c.locked_until_at > as_of)
             .order_by(schedule_commit.c.commit_id))
    for record in connection.execute(query).mappings():
        row = dict(record)
        if (row['run_experiment_key'] != experiment_key or row['berth_id'] != row['assignment_berth_id']
                or row['call_id'] not in ids):
            raise ValueError('Committed assignment does not match its experiment, berth or active call')
        commits.append(row)
    states = []
    for c in active:
        state = by_state.get(c['call_id'])
        if state is None:
            state = dict(state_id=None, call_id=c['call_id'], berth_id=None,
                         experiment_key=experiment_key, state_at=as_of,
                         status='WAITING' if c['available_at'] <= as_of else 'PLANNED',
                         remaining_work_kg=c['cargo_quantity_kg'], accrued_demurrage_cents=0,
                         contract_clock_state={'remaining_work_minutes': c['work_minutes'],
                                               'initialization': 'NEWLY_DISCLOSED_UNSTARTED'})
        states.append(state)
    weather_path = Path(weather_path).resolve()
    tentative=previous_tentative(connection,experiment_key,as_of,ids,{c['call_id'] for c in commits})
    snapshot = plain(dict(experiment_key=experiment_key, as_of_at=as_of,
                          berthing_policy=berthing_policy,
                          daylight_windows=fingerprint(ROOT/'src/daily_planning/daylight_windows.csv'),
                          calls=active, contracts=contracts, berths=berths, call_states=states,
                          source_state_ids=[s['state_id'] for s in stored_states], commits=commits,
                          previous_tentative_assignments=tentative,
                          weather=fingerprint(weather_path)))
    from .sk_physical_v3 import build_sk_snapshot_for_experiment
    physical = build_sk_snapshot_for_experiment(experiment_key, active, berths)
    if physical is not None:
        snapshot["sk_physical"] = physical

    snapshot["snapshot_hash"] = signature(snapshot)
    return snapshot


def verify_snapshot(snapshot):
    body = {k: v for k, v in snapshot.items() if k != 'snapshot_hash'}
    if signature(body) != snapshot.get('snapshot_hash'):
        raise ValueError('Frozen input snapshot hash mismatch')
    if fingerprint(snapshot['weather']['path']) != snapshot['weather']:
        raise ValueError('Weather file differs from the frozen input')
    if snapshot.get('daylight_windows') and fingerprint(snapshot['daylight_windows']['path']) != snapshot['daylight_windows']:
        raise ValueError('Sunrise/sunset file differs from the frozen input')
    if snapshot.get("sk_physical") is not None:
        from .sk_physical_v3 import verify_sk_snapshot
        verify_sk_snapshot(snapshot["sk_physical"])


def engine_calls(snapshot):
    terms = {c['contract_id']: c for c in snapshot['contracts']}
    calls = []
    for c in snapshot['calls']:
        source = terms[c['contract_id']]
        con = dict(source['contract_terms'])
        con.update(allowed_minutes=source['allowed_minutes'],
                   daily_demurrage_usd=str(Decimal(source['demurrage_daily_cents']) / 100),
                   nor_at=local_time(c['nor_at']).isoformat())
        calls.append(dict(call_id=c['call_id'], available_at=local_time(c['available_at']).isoformat(),
                          processing_minutes=c['work_minutes'], cargo_quantity_kg=c['cargo_quantity_kg'],
                          contract=con))
    return calls


def prepare_run(engine, experiment_key, as_of, *, run_id=None, horizon_hours=72,
                freeze_hours=24, top_k=3, weather_path=None, output_root=None):
    if (horizon_hours, freeze_hours) != (72, 24) or not 1 <= top_k <= 3:
        raise ValueError('This phase supports horizon=72h, freeze=24h and top_k=1..3')
    as_of = kst_time(as_of)
    local_time(as_of)
    run_id = uuid.UUID(str(run_id)) if run_id else uuid.uuid4()
    weather_path = Path(weather_path or ROOT/'input/weather_for_optimizer_30min.csv').resolve()
    folder = (Path(output_root or ROOT/'results/daily') / str(run_id)).resolve()
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            experiment_lock(connection, experiment_key)
            old = connection.execute(sa.select(planning_run).where(planning_run.c.run_id == run_id)).mappings().first()
            if old:
                if (old['experiment_key'] != experiment_key or old['planning_as_of'] != as_of
                        or old['weather_file_path'] != str(weather_path)
                        or old['run_config']['solver_config']['top_k'] != top_k):
                    raise ValueError('run_id already belongs to different inputs')
                config = old['run_config']
                verify_snapshot(config['input_snapshot'])
                if json.loads(Path(old['input_snapshot_path']).read_text()) != config['input_snapshot']:
                    raise ValueError('Snapshot file differs from PostgreSQL')
                return dict(run_id=str(run_id), output_path=str(Path(old['input_snapshot_path']).parent),
                            status=old['solver_status'], run_config=config, idempotent_retry=True)
            snapshot = read_snapshot(connection, experiment_key, as_of, weather_path)
            from weather_optimizer import WeatherSeries
            weather = WeatherSeries.from_csv(weather_path)
            if weather.first > local_time(as_of) or weather.end < local_time(as_of)+timedelta(hours=72):
                raise ValueError('Weather file must cover the complete 72h planning window')
            config = dict(schema_version=1, policies={
                'known_at': 'NOR_DAY_MIDNIGHT_KST', 'weather_information': 'PERFECT_INFORMATION',
                'availability': 'SCENARIO_NOR_PROXY',
                'remaining_quantity': 'CEIL_PROPORTIONAL_WITH_EXACT_WORK_MINUTES',
                'tail': 'FCFS_AVAILABLE_AT_CALL_ID_EARLIEST_FREE_BERTH'},
                solver_config=dict(horizon_hours=72, freeze_hours=24, top_k=top_k,
                                   cost_slack_fraction='0.03', deferred_objective='ITERATIVE_CP_SAT_EXACT_FCFS',
                                   stability_priority='BERTH_CHANGE_COUNT_THEN_START_SHIFT_MINUTES'),
                input_snapshot=snapshot)
            folder.mkdir(parents=True, exist_ok=False)
            snapshot_path = folder/'input_snapshot.json'
            snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2)+'\n')
            report = dict(run_id=str(run_id), experiment_key=experiment_key,
                          planning_as_of=as_of.isoformat(), horizon_end_at=(as_of+timedelta(hours=72)).isoformat(),
                          freeze_end_at=(as_of+timedelta(hours=24)).isoformat(),
                          status='INPUT_READY', run_config=config)
            (folder/'planning_context.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
            connection.execute(planning_run.insert().values(
                run_id=run_id, experiment_key=experiment_key, planning_as_of=as_of,
                horizon_end_at=as_of+timedelta(hours=72), freeze_end_at=as_of+timedelta(hours=24),
                input_snapshot_path=str(snapshot_path), weather_file_path=str(weather_path),
                run_config=config, solver_status='INPUT_READY'))
    return dict(run_id=str(run_id), output_path=str(folder), status='INPUT_READY',
                run_config=config, idempotent_retry=False)
