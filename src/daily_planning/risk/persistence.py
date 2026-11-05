"""Transactional, idempotent writes confined to Risk-owned PostgreSQL tables."""
from __future__ import annotations

import uuid
from collections.abc import Mapping

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from contracts import RULE_VERSION

from ..context import plain, signature
from ..models import (planning_run, risk_analysis, risk_candidate_outcome,
                      risk_scenario, risk_vessel_result, schedule_candidate)

REPLAY_VERSION = 'RISK_REPLAY_SECONDS_V2'


def _uuid(*parts) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, 'docktimize-risk:' + ':'.join(map(str, parts)))


def prepare_analysis_records(result: Mapping) -> dict:
    """Validate a complete A/B/C report and build deterministic immutable rows."""
    run_id = uuid.UUID(str(result['run_id']))
    refs = result['candidate_refs']
    if len(refs) != 3 or {ref['label'] for ref in refs} != {'A', 'B', 'C'}:
        raise ValueError('Risk persistence requires exact A/B/C candidate references')
    config = plain(result['scenario_config'])
    if config.get('provenance') != 'SYNTHETIC_ASSUMPTION':
        raise ValueError('Scenario provenance must be SYNTHETIC_ASSUMPTION')
    scenarios = result['scenarios']
    ids = [scenario['scenario_id'] for scenario in scenarios]
    if not ids or len(ids) != len(set(ids)) or result['scenario_count'] != len(ids):
        raise ValueError('Scenario IDs/count differ')
    if any(s.get('provenance') != 'SYNTHETIC_ASSUMPTION' for s in scenarios):
        raise ValueError('Unlabeled scenario input')
    outcomes = result['scenario_results']
    if set(outcomes) != {'A', 'B', 'C'} or any(set(outcomes[label]) != set(ids) for label in 'ABC'):
        raise ValueError('Missing or extra candidate/scenario outcomes')
    fingerprint = signature(dict(run_id=str(run_id),
        input_snapshot_hash=result['input_snapshot_hash'],
        replay_version=REPLAY_VERSION, contract_rule_version=RULE_VERSION,
        scenario_config=config, candidate_refs=refs))
    analysis_id = _uuid(fingerprint)
    summary = plain({key: value for key, value in result.items()
                     if key not in ('scenarios', 'scenario_results', 'scenario_config',
                                    'candidate_refs', 'failures', 'scenario_ids')})
    summary['failures'] = [{key: value for key, value in item.items()
                            if key != 'physical_replay'} for item in result.get('failures', [])]
    if result.get('full_distribution_available') is False and result.get('risk_by_candidate') is not None:
        raise ValueError('Partial outcomes cannot have full-distribution cost risk')
    analysis = dict(analysis_id=analysis_id, run_id=run_id,
        input_snapshot_hash=result['input_snapshot_hash'],
        analysis_fingerprint=fingerprint, result_hash=signature(summary),
        replay_version=REPLAY_VERSION, contract_rule_version=RULE_VERSION,
        model_id=config['model_id'], scenario_config=config,
        candidate_refs=plain(refs), summary=summary)
    scenario_rows, outcome_rows, vessel_rows = [], [], []
    ref_by_label = {ref['label']: ref for ref in refs}
    for scenario in scenarios:
        sid = scenario['scenario_id']
        payload = plain(scenario)
        scenario_rows.append(dict(risk_scenario_id=_uuid(analysis_id, sid),
            analysis_id=analysis_id, scenario_id=sid, payload=payload,
            content_hash=signature(payload)))
        for label in 'ABC':
            outcome = plain(outcomes[label][sid])
            vessels = outcome.pop('vessel_results', None)
            physical = outcome.pop('physical_replay', None)
            status = outcome.get('status')
            cost = outcome.get('cost_cents')
            if status == 'SUCCEEDED':
                if type(cost) is not int or cost < 0 or not isinstance(vessels, list):
                    raise ValueError(f'Invalid settled outcome: {label}/{sid}')
                if (any(type(v.get('demurrage_increment_to_completion_cents')) is not int
                        for v in vessels)
                        or sum(v['demurrage_increment_to_completion_cents'] for v in vessels) != cost):
                    raise ValueError(f'Vessel costs do not reconcile: {label}/{sid}')
            elif status in ('FAILED', 'MISSING'):
                if cost is not None or vessels is not None:
                    raise ValueError(f'Unpriced outcome has cost/details: {label}/{sid}')
                outcome['failure_class'] = outcome.get('failure_class') or (
                    'MISSING' if status == 'MISSING' else 'INFEASIBLE')
            else:
                raise ValueError(f'Unknown outcome status: {label}/{sid}')
            outcome_id = _uuid(analysis_id, sid, label)
            outcome_rows.append(dict(outcome_id=outcome_id, analysis_id=analysis_id,
                scenario_id=sid, candidate_id=uuid.UUID(str(ref_by_label[label]['candidate_id'])),
                label=label, status=status, cost_cents=cost,
                failure_class=outcome.get('failure_class'), payload=outcome,
                content_hash=signature(outcome)))
            detail = vessels if vessels is not None else physical or []
            if len({v['call_id'] for v in detail}) != len(detail):
                raise ValueError(f'Duplicate vessel detail: {label}/{sid}')
            for vessel in detail:
                row = plain(vessel)
                vessel_rows.append(dict(vessel_result_id=_uuid(outcome_id, row['call_id']),
                    outcome_id=outcome_id, call_id=row['call_id'], payload=row,
                    content_hash=signature(row)))
    return dict(analysis=analysis, scenarios=scenario_rows,
                outcomes=outcome_rows, vessels=vessel_rows)


def _insert_verified(connection, table, rows, id_name, hash_name='content_hash'):
    if not rows:
        return
    connection.execute(insert(table).on_conflict_do_nothing(index_elements=[id_name]), rows)
    for index in range(0, len(rows), 500):
        group = rows[index:index + 500]
        expected = {row[id_name]: row[hash_name] for row in group}
        stored = dict(connection.execute(sa.select(table.c[id_name], table.c[hash_name])
            .where(table.c[id_name].in_(expected))).all())
        if stored != expected:
            raise ValueError(f'Idempotent Risk write conflict in {table.name}')


def persist_risk_analysis(engine, result: Mapping) -> str:
    """Read source references, then write only the four Risk tables atomically."""
    rows = prepare_analysis_records(result)
    analysis = rows['analysis']
    with engine.begin() as connection:
        run = connection.execute(sa.select(planning_run).where(
            planning_run.c.run_id == analysis['run_id'])).mappings().one()
        stored_snapshot = run['run_config']['input_snapshot']
        if stored_snapshot['snapshot_hash'] != analysis['input_snapshot_hash']:
            raise ValueError('Immutable planning snapshot hash differs')
        refs = {uuid.UUID(str(ref['candidate_id'])): ref
                for ref in analysis['candidate_refs']}
        candidates = connection.execute(sa.select(schedule_candidate).where(
            schedule_candidate.c.run_id == analysis['run_id'])).mappings().all()
        if set(refs) != {item['candidate_id'] for item in candidates}:
            raise ValueError('Stored A/B/C candidate references differ')
        for candidate in candidates:
            metrics = candidate['evaluation_metrics']
            ref = refs[candidate['candidate_id']]
            if (metrics['schedule_signature'] != ref['schedule_signature']
                    or metrics['freeze_signature'] != ref['freeze_signature']
                    or 'ABC'[candidate['candidate_rank'] - 1] != ref['label']):
                raise ValueError('Candidate schedule/freeze version differs')
        _insert_verified(connection, risk_analysis, [analysis], 'analysis_id', 'result_hash')
        _insert_verified(connection, risk_scenario, rows['scenarios'], 'risk_scenario_id')
        _insert_verified(connection, risk_candidate_outcome, rows['outcomes'], 'outcome_id')
        _insert_verified(connection, risk_vessel_result, rows['vessels'], 'vessel_result_id')
    return str(analysis['analysis_id'])
