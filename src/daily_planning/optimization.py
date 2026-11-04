"""Generate A/B/C candidates, persist exact evaluations, and export comparisons."""
from __future__ import annotations

import csv
import json
import math
import time
import uuid
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa

from weather_optimizer import WeatherSeries

from .candidate_store import save_evaluated_candidate
from .context import verify_snapshot
from .evaluation import evaluate_baseline
from .exact_search import build_domain, cost_then_wait_search, exact_search
from .models import planning_run, schedule_candidate


def write_json(path,value):
    content=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
    if path.exists():
        # PostgreSQL JSONB does not preserve object key order. Compare the
        # serialized value on idempotent retries, leaving the original file.
        if json.loads(path.read_text())!=value:
            raise ValueError(f'Existing output differs; preserve it: {path}')
    else:
        with path.open('x') as handle:
            handle.write(content)


def write_csv(path,rows,columns):
    import io
    buffer=io.StringIO(newline='')
    writer=csv.DictWriter(buffer,fieldnames=columns,extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    content=buffer.getvalue()
    if path.exists():
        with path.open(newline='') as handle:
            if handle.read()!=content:
                raise ValueError(f'Existing output differs; preserve it: {path}')
    else:
        with path.open('x',newline='') as handle:
            handle.write(content)


def export_run(engine,run_id):
    with engine.connect() as connection:
        run=connection.execute(sa.select(planning_run).where(planning_run.c.run_id==run_id)).mappings().one()
        candidates=connection.execute(sa.select(schedule_candidate).where(schedule_candidate.c.run_id==run_id)
                                      .order_by(schedule_candidate.c.candidate_rank)).mappings().all()
    folder=Path(run['input_snapshot_path']).parent
    summary=run['run_config']['optimization']
    comparisons=[]
    vessels=[]
    base_cost=candidates[0]['simulated_demurrage_cents'] if candidates else 0
    base_freeze=candidates[0]['evaluation_metrics']['freeze_signature'] if candidates else None
    for candidate in candidates:
        metrics=candidate['evaluation_metrics']
        evaluation=next(e for e in metrics['evaluations'] if e['evaluation_id']==metrics['comparison_evaluation_id'])
        letter=chr(ord('A')+candidate['candidate_rank']-1)
        rows=evaluation['vessel_results']
        schedule=[r for r in rows if r['assignment_kind']!='EVALUATION_TAIL']
        schedule_columns=['call_id','assignment_kind','berth_id','work_start_at','completion_at',
                          'berth_release_at','demurrage_increment_to_completion_cents']
        write_csv(folder/f'candidate_{letter}_schedule.csv',schedule,schedule_columns)
        write_json(folder/f'candidate_{letter}_evaluation.json',evaluation)
        comparisons.append(dict(candidate_id=str(candidate['candidate_id']),label=letter,
            strategy=candidate['strategy'],expected_demurrage_cents=candidate['expected_demurrage_cents'],
            simulated_demurrage_cents=candidate['simulated_demurrage_cents'],
            cost_difference_from_A_cents=candidate['simulated_demurrage_cents']-base_cost,
            same_first_24h_as_A=metrics['freeze_signature']==base_freeze,
            solver_status=metrics['solver']['solver_status'],relative_gap=metrics['solver']['relative_gap'],
            **evaluation['summary']))
        vessels.extend(dict(candidate_id=str(candidate['candidate_id']),label=letter,**r) for r in rows)
    comparison_columns=['candidate_id','label','strategy','expected_demurrage_cents','simulated_demurrage_cents',
        'cost_difference_from_A_cents','total_wait_minutes','weather_pause_minutes','last_completion_at',
        'demurrage_increment_24h_cents','demurrage_increment_72h_cents','demurrage_increment_to_completion_cents',
        'remaining_work_kg_at_72h','unfinished_calls_at_72h','same_first_24h_as_A','solver_status','relative_gap']
    if summary.get('stability_enabled'):
        comparison_columns+=['berth_change_count','start_shift_minutes','compared_tentative_call_count']
    write_csv(folder/'candidates_summary.csv',comparisons,comparison_columns)
    write_csv(folder/'simulation_comparison.csv',comparisons,comparison_columns)
    write_csv(folder/'simulation_vessel_results.csv',vessels,['candidate_id','label','call_id','assignment_kind',
        'berth_id','work_start_at','completion_at','total_wait_minutes','weather_pause_minutes',
        'demurrage_increment_24h_cents','demurrage_increment_72h_cents','demurrage_increment_to_completion_cents',
        'total_settlement_demurrage_cents','remaining_work_kg_at_24h','remaining_work_kg_at_72h'])
    write_json(folder/'optimization_summary.json',summary)
    validation=dict(status='PASS',run_id=str(run_id),
        candidates=len(candidates),all_baselines_replayed=True,
        expected_matches_simulated=all(c['expected_demurrage_cents']==c['simulated_demurrage_cents'] for c in candidates),
        distinct_signatures=len({c['evaluation_metrics']['schedule_signature'] for c in candidates})==len(candidates),
        operating_state_changed=False,confirmed=False)
    write_json(folder/'optimization_validation.json',validation)
    if not (folder/'validation.json').exists():
        write_json(folder/'validation.json',validation)
    write_json(folder/'execution.log',dict(phase='CANDIDATE_GENERATION',run_id=str(run_id),
                                         strategies=summary['strategies']))
    return dict(run_id=str(run_id),output_path=str(folder),candidate_count=len(candidates),
                candidates=[dict(candidate_id=str(c['candidate_id']),strategy=c['strategy'],
                                  demurrage_cents=c['simulated_demurrage_cents']) for c in candidates],
                pending=summary['pending'])


def optimize_run(engine,run_id,*,time_limit=180,cost_slack='0.03'):
    run_id=uuid.UUID(str(run_id))
    slack=Decimal(str(cost_slack))
    if not slack.is_finite() or slack<0 or not math.isfinite(time_limit) or time_limit<=0:
        raise ValueError('Invalid cost slack or time limit')
    with engine.connect() as connection:
        run=connection.execute(sa.select(planning_run).where(planning_run.c.run_id==run_id)).mappings().one()
    config=run['run_config']
    if 'optimization' in config:
        old=config['optimization']
        if old['time_limit_per_candidate_seconds']!=time_limit or old['cost_slack_fraction']!=str(slack):
            raise ValueError('Run already optimized with different settings; create a new run')
        return export_run(engine,run_id)
    snapshot=config['input_snapshot']
    verify_snapshot(snapshot)
    weather=WeatherSeries.from_csv(Path(snapshot['weather']['path']))
    t0=time.monotonic()
    domain=build_domain(snapshot,weather)
    domain_seconds=time.monotonic()-t0
    first,a_record=cost_then_wait_search(snapshot,weather,domain,time_limit=time_limit)
    if first is None:
        raise RuntimeError(f'No evaluated feasible A candidate: {a_record}')
    generated=[(1,'MIN_DEMURRAGE',first,a_record)]
    cost_a=first['evaluation']['summary']['demurrage_increment_to_completion_cents']
    cap=int(Decimal(cost_a)*(1+slack))
    records=[a_record]
    if config['solver_config']['top_k']>=2:
        second,b_record=exact_search(snapshot,weather,domain,objective='wait',time_limit=time_limit,
            cost_cap=cap,excluded_signatures=[first['schedule_signature']])
        records.append(b_record)
        if second is not None:
            generated.append((2,'MIN_WAIT_WITH_COST_LIMIT',second,b_record))
    if config['solver_config']['top_k']>=3:
        has_previous=bool(snapshot.get('previous_tentative_assignments'))
        objective='stability' if has_previous else 'wait'
        third,c_record=exact_search(snapshot,weather,domain,objective=objective,time_limit=time_limit,
            cost_cap=cap,excluded_signatures=[r[2]['schedule_signature'] for r in generated])
        c_record['strategy']='MIN_SCHEDULE_CHANGES'
        c_record['first_run_fallback']=not has_previous
        records.append(c_record)
        if third is not None:
            generated.append((3,'MIN_SCHEDULE_CHANGES',third,c_record))
    summary=dict(schema_version=1,run_id=str(run_id),algorithm='ITERATIVE_CP_SAT_EXACT_FCFS',
        option_count=len(domain['options']),candidate_count=len(generated),requested_top_k=config['solver_config']['top_k'],
        domain_generation_seconds=domain_seconds,time_limit_per_candidate_seconds=time_limit,
        cost_slack_fraction=str(slack),cost_cap_cents=cap,strategies=records,
        cost_definition='Demurrage increment from planning_as_of to completion for known active calls',
        stability_enabled=True,pending=[])
    if snapshot.get('berth_phase'):
        summary['berth_phase_policy'] = snapshot['berth_phase']
    if snapshot.get('sk_physical'):
        physical=snapshot['sk_physical']
        summary['sk_physical']=dict(
            mode=physical['policy'],
            data_origin='SYNTHETIC',
            compatibility_scope='DOCUMENTED_PHYSICAL_ONLY',
            profile_count=len(physical['profiles']),
            not_evaluated=physical['not_evaluated'])
    prepared=[]
    for index,strategy,result,record in generated:
        rows=[r for r in result['evaluation']['vessel_results'] if r['assignment_kind']=='PLANNED']
        # Independent final replay from the same frozen input, not the master bound.
        final=evaluate_baseline(snapshot,rows,weather)
        expected=result['evaluation']['summary']['demurrage_increment_to_completion_cents']
        if (final['schedule_signature']!=result['schedule_signature']
                or final['evaluation']['summary']!=result['evaluation']['summary']):
            raise AssertionError('Independent final BASELINE replay differs from exact search evaluation')
        prepared.append((uuid.uuid5(run_id,strategy),index,strategy,expected,final,record))
    with engine.begin() as connection:
        current=connection.execute(sa.select(planning_run).where(planning_run.c.run_id==run_id)
                                   .with_for_update()).mappings().one()
        if 'optimization' in current['run_config']:
            # Another process completed this same run while the solver was busy.
            old=current['run_config']['optimization']
            if old['time_limit_per_candidate_seconds']!=time_limit or old['cost_slack_fraction']!=str(slack):
                raise ValueError('Another process completed this run with different settings')
        else:
            if current['run_config']['input_snapshot']!=snapshot:
                raise ValueError('Frozen input changed while optimizing')
            for cid,rank,strategy,expected,final,record in prepared:
                save_evaluated_candidate(connection,run_id,candidate_id=cid,strategy=strategy,
                    candidate_rank=rank,expected_demurrage_cents=expected,evaluation_result=final,solver_details=record)
            updated=dict(current['run_config'],optimization=summary)
            connection.execute(planning_run.update().where(planning_run.c.run_id==run_id)
                               .values(run_config=updated,solver_status=a_record['solver_status']))
    return export_run(engine,run_id)
