"""Read-only recovery of previously selected plan provenance."""
import uuid
import sqlalchemy as sa
from ..models import planning_run, schedule_candidate, schedule_assignment
from .validator import forecast_time
from .service import generate_forecast


def forecast_for_run(engine, run_id):
    with engine.connect() as connection:
        run = connection.execute(sa.select(planning_run).where(planning_run.c.run_id == uuid.UUID(str(run_id)))).mappings().one()
        snapshot = run['run_config']['input_snapshot']
        assignments = {}
        for item in snapshot.get('previous_tentative_assignments', []):
            try: assignments[uuid.UUID(str(item['assignment_id']))] = item
            except (KeyError, ValueError, TypeError): continue
        provenance = {}
        if assignments:
            rows = connection.execute(sa.select(schedule_assignment, planning_run.c.experiment_key,
                planning_run.c.planning_as_of, planning_run.c.run_config).join(
                schedule_candidate, schedule_assignment.c.candidate_id == schedule_candidate.c.candidate_id).join(
                planning_run, schedule_candidate.c.run_id == planning_run.c.run_id).where(
                schedule_assignment.c.assignment_id.in_(assignments))).mappings()
            for row in rows:
                item = assignments[row['assignment_id']]
                confirmation = row['run_config'].get('confirmation', {})
                if (row['experiment_key'] != run['experiment_key'] or
                    forecast_time(row['planning_as_of']) >= forecast_time(snapshot['as_of_at']) or
                    confirmation.get('candidate_id') != str(row['candidate_id']) or
                    str(item.get('candidate_id')) != str(row['candidate_id']) or
                    item.get('call_id') != row['call_id'] or item.get('berth_id') != row['berth_id'] or
                    not item.get('start_at') or not item.get('completion_at') or
                    forecast_time(item['start_at']) != forecast_time(row['start_at']) or
                    forecast_time(item['completion_at']) != forecast_time(row['completion_at']) or
                    not confirmation.get('confirmed_at')):
                    continue
                if forecast_time(confirmation['confirmed_at']) <= forecast_time(snapshot['as_of_at']):
                    provenance[str(row['assignment_id'])] = {'confirmed_at': confirmation['confirmed_at']}
        return generate_forecast(snapshot, selection_provenance=provenance)
