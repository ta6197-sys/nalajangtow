"""Replay only confirmed work through an operating cut; never dispatch tail work."""
from decimal import Decimal

from rolling_horizon import workload_remaining

from .bootstrap_db import kst_time, remaining_kg
from .context import engine_calls,local_time,plain
from .evaluation import price_until,replay_row,interval_overlap


def execute_committed_window(snapshot,end,weather):
    begin=local_time(snapshot['as_of_at'])
    cut=local_time(end)
    states={s['call_id']:s for s in snapshot['call_states']}
    commits={c['call_id']:c for c in snapshot['commits']}
    if len(commits)!=len(snapshot['commits']):
        raise ValueError('Duplicate committed call')
    details=[]
    executions=[]
    for call in engine_calls(snapshot):
        cid=call['call_id']
        protected=commits.get(cid)
        row=None
        if protected and local_time(protected['start_at'])<cut:
            row=replay_row(call,protected['berth_id'],protected['start_at'],weather,policy=snapshot.get('berthing_policy'))
            if (local_time(row['completion_at'])!=local_time(protected['completion_at'])
                    or local_time(protected['locked_from_at'])>local_time(row['work_start_at'])
                    or local_time(protected['locked_until_at'])<local_time(row['completion_at'])):
                raise ValueError('Protected work differs from confirmed occupancy')
            executions.append(row)
        if states[cid]['status']=='IN_PROGRESS':
            if row is None or states[cid]['berth_id']!=row['berth_id']:
                raise ValueError('In-progress work lost its protected berth')
            before_remaining=workload_remaining(row,call,begin)
            if (before_remaining!=states[cid]['contract_clock_state']['remaining_work_minutes']
                    or remaining_kg(call['cargo_quantity_kg'],before_remaining,call['processing_minutes'])
                    !=states[cid]['remaining_work_kg']):
                raise ValueError('Carried workload differs from confirmed work')
        before=price_until(call,row,begin,weather)
        if before['demurrage_cents']!=states[cid]['accrued_demurrage_cents']:
            raise ValueError('Carried demurrage differs from its contract clock')
        # Waiting calls require no fabricated completion or future weather replay.
        after=price_until(call,row,min(cut,local_time(row['completion_at'])) if row else cut,weather)
        delta=after['demurrage_cents']-before['demurrage_cents']
        if delta<0:
            raise ValueError('Cumulative demurrage cannot decrease')
        remaining=workload_remaining(row,call,cut) if row else call['processing_minutes']
        completed=row is not None and local_time(row['completion_at'])<=cut
        details.append(dict(call_id=cid,berth_id=row['berth_id'] if row else None,
            work_start_at=kst_time(row['work_start_at']).isoformat() if row else None,
            completion_at=kst_time(row['completion_at']).isoformat() if row else None,
            weather_pause_intervals=[[kst_time(a).isoformat(),kst_time(b).isoformat()]
                                     for a,b in row['weather_pause_intervals']] if row else [],
            work_intervals=row.get('work_intervals', []) if row else [],
            work_pause_intervals=row.get('work_pause_intervals', row['weather_pause_intervals']) if row else [],
            night_pause_intervals=row.get('night_pause_intervals', []) if row else [],
            accrued_before_cents=before['demurrage_cents'],demurrage_increment_24h_cents=delta,
            despatch_to_completion_cents=after['despatch_cents'] if completed else 0,
            remaining_work_minutes_at_24h=remaining,
            remaining_work_kg_at_24h=remaining_kg(call['cargo_quantity_kg'],remaining,call['processing_minutes']),
            contract_clock_at_24h=plain({k:str(Decimal(str(v))) if isinstance(v,float) else v
                                       for k,v in after.items()})))
    if any(interval_overlap(a,b) for i,a in enumerate(executions) for b in executions[i+1:]):
        raise ValueError('Confirmed work overlaps on a berth')
    return dict(vessel_results=details,
                summary={'demurrage_increment_24h_cents':sum(r['demurrage_increment_24h_cents'] for r in details)})
