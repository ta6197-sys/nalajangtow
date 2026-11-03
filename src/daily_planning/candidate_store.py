"""Append-only evaluation history and immutable candidate assignments."""
from __future__ import annotations

import copy
import uuid

import sqlalchemy as sa

from .bootstrap_db import kst_time
from .context import verify_snapshot
from .models import planning_run, schedule_assignment, schedule_candidate


def save_evaluated_candidate(connection, run_id, *, candidate_id, strategy, candidate_rank,
                             expected_demurrage_cents, evaluation_result, solver_details):
    """Caller owns the transaction. This never confirms or advances operation."""
    run_id, candidate_id = uuid.UUID(str(run_id)), uuid.UUID(str(candidate_id))
    run = connection.execute(sa.select(planning_run).where(planning_run.c.run_id == run_id)
                             .with_for_update()).mappings().one()
    snapshot = run['run_config']['input_snapshot']
    verify_snapshot(snapshot)
    evaluation = evaluation_result['evaluation']
    if (evaluation['input_snapshot_hash'] != snapshot['snapshot_hash']
            or evaluation['case'] != 'BASELINE' or evaluation['status'] != 'SUCCEEDED'):
        raise ValueError('Evaluation does not match this run and BASELINE')
    if type(expected_demurrage_cents) is not int or expected_demurrage_cents < 0:
        raise ValueError('Expected demurrage must be integer nonnegative cents')
    if evaluation['summary']['constraint_violations']:
        raise ValueError('Invalid candidates cannot be stored as evaluated schedules')
    existing = connection.execute(sa.select(schedule_candidate)
                                  .where(schedule_candidate.c.candidate_id == candidate_id)).mappings().first()
    if existing:
        metrics = copy.deepcopy(existing['evaluation_metrics'])
        if (existing['run_id'] != run_id or existing['strategy'] != strategy
                or existing['candidate_rank'] != candidate_rank
                or existing['expected_demurrage_cents'] != expected_demurrage_cents
                or metrics['schedule_signature'] != evaluation_result['schedule_signature']
                or metrics['solver'] != solver_details):
            raise ValueError('Candidate identity is immutable')
        for old in metrics['evaluations']:
            if old['evaluation_id'] == evaluation['evaluation_id']:
                if old != evaluation:
                    raise ValueError('Evaluation ID already has different results')
                return dict(candidate_id=str(candidate_id), idempotent_retry=True)
        metrics['evaluations'].append(copy.deepcopy(evaluation))
    else:
        duplicate = connection.execute(sa.select(schedule_candidate.c.candidate_id)
                    .where(schedule_candidate.c.run_id == run_id,
                           schedule_candidate.c.evaluation_metrics['schedule_signature'].astext
                           == evaluation_result['schedule_signature'])).first()
        if duplicate:
            raise ValueError('Duplicate schedule in the same planning run')
        metrics = dict(schema_version=1, schedule_signature=evaluation_result['schedule_signature'],
                       freeze_signature=evaluation_result['freeze_signature'],
                       deferred_call_ids=evaluation_result['deferred_call_ids'],
                       solver=copy.deepcopy(solver_details), evaluations=[copy.deepcopy(evaluation)])
    metrics['comparison_evaluation_id'] = evaluation['evaluation_id']
    values = dict(evaluation_metrics=metrics,
                  simulated_demurrage_cents=evaluation['summary']['demurrage_increment_to_completion_cents'])
    if existing:
        connection.execute(schedule_candidate.update().where(schedule_candidate.c.candidate_id == candidate_id).values(**values))
    else:
        connection.execute(schedule_candidate.insert().values(
            candidate_id=candidate_id, run_id=run_id, strategy=strategy, candidate_rank=candidate_rank,
            expected_demurrage_cents=expected_demurrage_cents, **values))
        for row in evaluation['vessel_results']:
            if row['assignment_kind'] == 'EVALUATION_TAIL':
                continue
            connection.execute(schedule_assignment.insert().values(
                candidate_id=candidate_id, call_id=row['call_id'], berth_id=row['berth_id'],
                start_at=kst_time(row['work_start_at']), completion_at=kst_time(row['completion_at'])))
    return dict(candidate_id=str(candidate_id), idempotent_retry=False)
