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


def get_risk_distribution_payload(engine, analysis_id: str) -> dict:
    aid = uuid.UUID(str(analysis_id))
    with engine.connect() as connection:
        row = connection.execute(sa.select(risk_analysis).where(
            risk_analysis.c.analysis_id == aid)).mappings().one()
        outcomes = connection.execute(sa.select(
            risk_candidate_outcome.c.scenario_id, risk_candidate_outcome.c.label,
            risk_candidate_outcome.c.status, risk_candidate_outcome.c.cost_cents,
            risk_candidate_outcome.c.failure_class).where(
            risk_candidate_outcome.c.analysis_id == aid).order_by(
            risk_candidate_outcome.c.scenario_id,
            risk_candidate_outcome.c.label)).mappings().all()
    summary = row['summary']
    return dict(analysis_id=str(aid), run_id=str(row['run_id']),
        input_snapshot_hash=row['input_snapshot_hash'],
        replay_version=row['replay_version'],
        contract_rule_version=row['contract_rule_version'],
        scenario_config=row['scenario_config'], candidate_refs=row['candidate_refs'],
        created_at=row['created_at'].isoformat(), summary=summary,
        outcomes=[dict(item) for item in outcomes])


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
