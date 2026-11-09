"""SELECT-only Risk API payload builders."""
from __future__ import annotations

import uuid

import sqlalchemy as sa

from ..models import risk_analysis, risk_candidate_outcome, risk_scenario, risk_vessel_result
from .candidate_reader import load_run_candidates
from .tradeoff import compare_candidates


def get_tradeoff_payload(engine, run_id: str) -> dict:
    run = load_run_candidates(engine, run_id)
    return {"run_id": run["run_id"], "data_origin": "EXISTING_BASELINE_EVALUATION",
            **compare_candidates(run["candidates"])}


def list_risk_analyses(engine, run_id: str) -> dict:
    rid = uuid.UUID(str(run_id))
    with engine.connect() as connection:
        rows = connection.execute(sa.select(risk_analysis).where(
            risk_analysis.c.run_id == rid).order_by(
            risk_analysis.c.created_at.desc())).mappings().all()
    return {'run_id': str(rid), 'analyses': [dict(
        analysis_id=str(row['analysis_id']), created_at=row['created_at'].isoformat(),
        model_id=row['model_id'], input_snapshot_hash=row['input_snapshot_hash'],
        scenario_count=row['summary']['scenario_count'],
        risk_status=row['summary']['risk_status'],
        full_distribution_available=row['summary']['full_distribution_available'])
        for row in rows]}


def _shock_index_item(row: dict) -> dict:
    """Compact index of sampled shocks; event frequencies are synthetic, not observed."""
    payload = row['payload']
    arrival = sorted((dict(call_id=call_id,
                           minutes=int(values.get('available_at_shift_minutes', 0)))
                      for call_id, values in payload.get('call_shocks', {}).items()
                      if int(values.get('available_at_shift_minutes', 0)) > 0),
                     key=lambda item: (-item['minutes'], item['call_id']))
    work = sorted((dict(call_id=call_id,
                        multiplier=float(values.get('work_duration_multiplier', 1)))
                   for call_id, values in payload.get('call_shocks', {}).items()
                   if float(values.get('work_duration_multiplier', 1)) > 1),
                  key=lambda item: (-item['multiplier'], item['call_id']))
    weather = []
    main = int(payload.get('additional_weather_block_minutes') or 0)
    if main > 0:
        weather.append(dict(duration_minutes=main,
                            onset_offset_minutes=payload.get('weather_block_onset_offset_minutes')))
    for block in payload.get('weather_blocks', []):
        if int(block.get('duration_minutes', 0)) > 0:
            weather.append(dict(duration_minutes=int(block['duration_minutes']),
                                onset_offset_minutes=block.get('onset_offset_minutes')))
    kinds = ([kind for kind, active in (('arrival', arrival), ('work', work),
                                        ('weather', weather)) if active])
    return dict(scenario_id=row['scenario_id'],
                scenario_weight=str(payload['scenario_weight']),
                provenance=payload.get('provenance'),
                types=kinds, mixed=len(kinds) > 1,
                arrival_count=len(arrival), work_count=len(work),
                arrival=arrival[:8], work=work[:8], weather=weather)


def get_risk_distribution_payload(engine, analysis_id: str) -> dict:
    aid = uuid.UUID(str(analysis_id))
    with engine.connect() as connection:
        row = connection.execute(sa.select(risk_analysis).where(
            risk_analysis.c.analysis_id == aid)).mappings().one()
        outcomes = connection.execute(sa.select(
            risk_candidate_outcome.c.scenario_id, risk_candidate_outcome.c.label,
            risk_candidate_outcome.c.status, risk_candidate_outcome.c.cost_cents,
            risk_candidate_outcome.c.failure_class,
            risk_candidate_outcome.c.payload).where(
            risk_candidate_outcome.c.analysis_id == aid).order_by(
            risk_candidate_outcome.c.scenario_id,
            risk_candidate_outcome.c.label)).mappings().all()
        scenarios = connection.execute(sa.select(
            risk_scenario.c.scenario_id, risk_scenario.c.payload).where(
            risk_scenario.c.analysis_id == aid).order_by(
            risk_scenario.c.scenario_id)).mappings().all()
    summary = row['summary']
    compact = []
    for item in outcomes:
        payload = item['payload'] or {}
        compact.append(dict(scenario_id=item['scenario_id'], label=item['label'],
            status=item['status'], cost_cents=item['cost_cents'],
            failure_class=item['failure_class'],
            propagated_delay_call_ids=payload.get('propagated_delay_call_ids'),
            commitment_status=payload.get('commitment_status'),
            total_start_delay_minutes=payload.get('total_start_delay_minutes')))
    return dict(analysis_id=str(aid), run_id=str(row['run_id']),
        input_snapshot_hash=row['input_snapshot_hash'],
        replay_version=row['replay_version'],
        contract_rule_version=row['contract_rule_version'],
        scenario_config=row['scenario_config'], candidate_refs=row['candidate_refs'],
        created_at=row['created_at'].isoformat(), summary=summary,
        outcomes=compact, scenario_index=[_shock_index_item(item) for item in scenarios])


def get_risk_scenario_payload(engine, analysis_id: str, scenario_id: str) -> dict:
    aid = uuid.UUID(str(analysis_id))
    with engine.connect() as connection:
        scenario = connection.execute(sa.select(risk_scenario).where(
            risk_scenario.c.analysis_id == aid,
            risk_scenario.c.scenario_id == scenario_id)).mappings().one()
        outcomes = connection.execute(sa.select(risk_candidate_outcome).where(
            risk_candidate_outcome.c.analysis_id == aid,
            risk_candidate_outcome.c.scenario_id == scenario_id).order_by(
            risk_candidate_outcome.c.label)).mappings().all()
        result = []
        for item in outcomes:
            vessels = connection.execute(sa.select(risk_vessel_result.c.payload).where(
                risk_vessel_result.c.outcome_id == item['outcome_id']).order_by(
                risk_vessel_result.c.call_id)).scalars().all()
            result.append(dict(label=item['label'], candidate_id=str(item['candidate_id']),
                status=item['status'], cost_cents=item['cost_cents'],
                failure_class=item['failure_class'], payload=item['payload'],
                vessel_results=vessels))
    return dict(analysis_id=str(aid), scenario_id=scenario_id,
                scenario=scenario['payload'], outcomes=result)
