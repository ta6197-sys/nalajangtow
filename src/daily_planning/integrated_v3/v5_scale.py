"""V5 staged, source-verified Rolling Horizon execution.

This module is deliberately an orchestration layer. It neither replaces the
Stage 1-4 optimizer nor relaxes missing operational policies. All outcomes are
synthetic, and unfinished rolling runs are explicitly incomplete.
"""
from __future__ import annotations

import json
import math
import os
import uuid
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any, Callable
from zoneinfo import ZoneInfo

VERSION = 'V5_STAGED_EXECUTION_V1'
BALANCED_EXT_PROFILE='V5_BALANCED_154_EXT_20270109'
KST = ZoneInfo('Asia/Seoul')
GATES = {'S5': (5, 2), 'S10': (10, 3), 'S20': (20, 3), 'R7': (20, 7), 'R14': (20, 14), 'FULL140': (140, 60)}
BALANCED_GATES = {**{k:v for k,v in GATES.items() if k!='FULL140'}, 'FULL154': (154,60)}
MAX_WINDOWS = 65
MAX_CALLS = 140


class ScaleInputError(ValueError):
    """Fail closed on missing or unsupported execution inputs."""


def read_json(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise ScaleInputError('Missing configuration: ' + str(p))
    if p.stat().st_size > 8_000_000:
        raise ScaleInputError('Configuration too large')
    value = json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ScaleInputError('Configuration must be a JSON object')
    return value


def atomic_json(path: Path, obj: Any) -> None:
    """Never mutate a previous artifact or expose a partial JSON file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        raise FileExistsError('Refusing to overwrite: ' + str(p))
    temp = p.with_name('.' + p.name + '.writing')
    with temp.open('x', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, p)


def _absolute(value: str, base: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ScaleInputError('A nonempty path is required')
    p = Path(value).expanduser()
    return (p if p.is_absolute() else base / p).resolve()


def load_execution_config(path: str | Path) -> dict:
    """Resolve explicit source, policy and pilotage inputs, without defaults.

    No generated policy, invented process flow, or implicit settlement evidence.
    """
    config_path = Path(path).expanduser().resolve()
    raw = read_json(config_path)
    if raw.get('version') != VERSION:
        raise ScaleInputError('Expected configuration version ' + VERSION)
    scenario=raw.get('scenario','V5_3TANK')
    if scenario not in ('V5_3TANK','V5_BALANCED_154',BALANCED_EXT_PROFILE):
        raise ScaleInputError('Unknown V5 scenario')
    base = config_path.parent
    for key in ('data_dir', 'policy_file', 'pilotage_context_file', 'as_of_at'):
        if key not in raw:
            raise ScaleInputError('Missing required configuration key: ' + key)
    data_dir = _absolute(raw['data_dir'], base)
    policy_path = _absolute(raw['policy_file'], base)
    pilot_path = _absolute(raw['pilotage_context_file'], base)
    if not data_dir.is_dir():
        raise ScaleInputError('V5 source input directory is unavailable: ' + str(data_dir))
    policy, pilotage = read_json(policy_path), read_json(pilot_path)
    for key in ('daylight_path', 'daylight_source', 'night_berthing_permissions',
                'departure_windows_by_berth_call'):
        if key not in pilotage:
            raise ScaleInputError('Missing explicit pilotage context: ' + key)
    pilotage['daylight_path'] = str(_absolute(pilotage['daylight_path'], pilot_path.parent))
    if not Path(pilotage['daylight_path']).is_file():
        raise ScaleInputError('Daylight input not found')
    if not isinstance(raw['as_of_at'], str) or not raw['as_of_at']:
        raise ScaleInputError('as_of_at must be an explicit timezone-aware string')
    at = datetime.fromisoformat(raw['as_of_at'])
    if at.tzinfo is None or at.utcoffset() is None:
        raise ScaleInputError('as_of_at must have a timezone offset')
    price = raw.get('pricing_policy', 'EXPLICIT_EVENTS_V1')
    if price not in ('EXPLICIT_EVENTS_V1', 'CLEAR_WEATHER_BASELINE_V1'):
        raise ScaleInputError('Unknown pricing_policy')
    # The clear-weather policy is synthetic by definition. It cannot be labelled actual.
    settlement_records = raw.get('settlement_records', [])
    if not isinstance(settlement_records, list):
        raise ScaleInputError('settlement_records must be an array')
    inventory_policy=raw.get('inventory_policy_version')
    risk_policy=raw.get('inventory_risk_policy_version')
    deferral_policy=raw.get('deferral_policy_version')
    if risk_policy is not None:
        from .v5_inventory_risk import RISK_VERSION
        if risk_policy!=RISK_VERSION or inventory_policy is None:
            raise ScaleInputError('Risk policy requires opt-in physical bounds')
    if deferral_policy is not None:
        from .v5_deferral import DEFERRAL_VERSION
        if deferral_policy!=DEFERRAL_VERSION or scenario not in ('V5_BALANCED_154',BALANCED_EXT_PROFILE):
            raise ScaleInputError('Invalid rolling deferral policy')
    max_probes=raw.get('max_deferral_probes',8)
    if type(max_probes) is not int or not 1<=max_probes<=20:
        raise ScaleInputError('Invalid deferral attempt count')
    if inventory_policy is not None:
        from .v5_inventory_policy import POLICY_VERSION
        if inventory_policy!=POLICY_VERSION or scenario not in ('V5_BALANCED_154',BALANCED_EXT_PROFILE):
            raise ScaleInputError('Unsupported inventory policy or scenario')
    return dict(data_dir=data_dir, policy=policy, pilotage_context=pilotage, scenario=scenario,
                as_of_at=at.isoformat(), pricing_policy=price,
                clock_policy=raw.get('clock_policy', 'BLOCK_UNVERIFIED_CLOCK'),
                settlement_records=settlement_records,
                config_file=str(config_path),
                **({'inventory_policy_version':inventory_policy} if inventory_policy is not None else {}),
                **({'inventory_risk_policy_version':risk_policy} if risk_policy is not None else {}),
                **({'deferral_policy_version':deferral_policy,'max_deferral_probes':max_probes} if deferral_policy is not None else {}))


def preflight_sources(config: dict) -> tuple[dict, dict]:
    """Load the *complete* original V5 input and verify its authoritative hashes.

    Do not subset the normalized snapshot; pass selected call IDs separately.
    """
    from ..integrated_input.loader import load_inputs
    from ..integrated_input.normalizer import normalize
    from ..integrated_input.v5 import validate_v5
    scenario=config.get('scenario','V5_3TANK')
    if scenario not in ('V5_3TANK','V5_BALANCED_154',BALANCED_EXT_PROFILE):raise ScaleInputError('Unknown V5 scenario')
    bundle = load_inputs(config['data_dir'], scenario=scenario)
    snapshot = normalize(bundle)
    report = validate_v5(snapshot)
    if not report['data_preflight_pass']:
        raise ScaleInputError('V5 source preflight failed: ' + str(report.get('errors')))
    expected=154 if scenario in ('V5_BALANCED_154',BALANCED_EXT_PROFILE) else 140
    if len(snapshot['calls']) != expected or len(snapshot['berths']) != 11 or len(snapshot['tanks']) != 3:
        raise ScaleInputError(f'Expected {expected} calls, 11 berths and 3 tanks')
    return snapshot, report


def select_scope(snapshot: dict, count: int, explicit_ids: list[str] | None = None) -> list[str]:
    """Deterministic earliest-ETA subset; no synthetic missing vessels.

    A subset is strictly a diagnostic scenario, not full-port completeness.
    """
    calls = snapshot['calls']
    by_id = {c['call_id']: c for c in calls}
    if len(by_id) != len(calls):
        raise ScaleInputError('Duplicate call IDs')
    if explicit_ids is not None:
        if (not isinstance(explicit_ids, list) or not explicit_ids or
                len(explicit_ids) != len(set(explicit_ids)) or not set(explicit_ids) <= set(by_id)):
            raise ScaleInputError('Invalid call_ids scope')
        if len(explicit_ids) > (154 if snapshot.get('scenario_id')=='V5_BALANCED_154' else MAX_CALLS):
            raise ScaleInputError('Too many call IDs')
        return sorted(explicit_ids)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= (154 if snapshot.get('scenario_id')=='V5_BALANCED_154' else MAX_CALLS):
        raise ScaleInputError('call_count outside scenario bounds')
    from ..integrated_input.validator import timestamp
    order = sorted(calls, key=lambda c: (timestamp(c['eta_at']), c['call_id']))
    return sorted(c['call_id'] for c in order[:count])


def bounded_parameters(*, count: int, windows: int, time_limit_seconds: float,
                       candidates_per_pair: int, total_candidates: int,
                       improvement_rounds: int, scenario: str = 'V5_3TANK') -> None:
    if any(isinstance(x, bool) or not isinstance(x, int) for x in (count, windows, candidates_per_pair, total_candidates, improvement_rounds)):
        raise ScaleInputError('Integer bounds required')
    if scenario not in ('V5_3TANK','V5_BALANCED_154',BALANCED_EXT_PROFILE) or not 1 <= count <= (154 if scenario in ('V5_BALANCED_154',BALANCED_EXT_PROFILE) else MAX_CALLS) or not 1 <= windows <= MAX_WINDOWS:
        raise ScaleInputError('Scope exceeds supported diagnostic bound')
    if not 1 <= candidates_per_pair <= 24 or not 1 <= total_candidates <= 12000:
        raise ScaleInputError('Invalid candidate limits')
    if not 1 <= improvement_rounds <= 2:
        raise ScaleInputError('Invalid candidate improvement rounds')
    if isinstance(time_limit_seconds, bool) or not isinstance(time_limit_seconds, (int, float)) or not math.isfinite(time_limit_seconds) or not 1 <= time_limit_seconds <= 1800:
        raise ScaleInputError('Solver budget must be 1..1800 seconds per window')


def summarize_result(result: dict, scope: list[str], *, seconds: float) -> dict:
    summaries = result.get('window_results') or []
    status = result.get('status')
    validated = result.get('validated_window_count', 0)
    all_processed = set(scope) <= set(result.get('completed_call_ids', []))
    return {'status': status, 'validated_window_count': validated,
            'requested_scope_count': len(scope), 'completed_scope_count': len(set(scope) & set(result.get('completed_call_ids', []))),
            'all_scope_calls_completed': all_processed, 'all_requested_windows_validated': status == 'REQUESTED_WINDOWS_VALIDATED',
            'cumulative_demurrage_cents': result.get('cumulative_demurrage_cents'),
            'cumulative_demurrage_scope': result.get('cumulative_demurrage_scope'),
            'unresolved_accounting_call_ids': result.get('unresolved_accounting_call_ids', []),
            'window_projected_costs_cents_NOT_ADDITIVE': [s.get('window_demurrage_cents') for s in summaries],
            'window_solver_statuses': [s.get('solver_status') for s in summaries],
            'final_domain_proofs': [s.get('final_domain_optimality_proven') for s in summaries],
            'windows': [{'window_id': s.get('window_id'), 'as_of_at': s.get('as_of_at'),
                         'selected_calls': len(s.get('selected_call_ids', [])),
                         'candidate_count_initial': s.get('initial_candidate_count'),
                         'candidate_count_final': s.get('final_candidate_count'),
                         'solver_calls': s.get('cost_candidates_evaluated'),
                         'runtime_seconds': s.get('optimization_runtime_seconds')} for s in summaries],
            'stopping_reason': result.get('stopping_reason'), 'total_elapsed_seconds': seconds,
            'simulation_only': True, 'operational_approval': False,
            'full_period_global_optimality_proven': False}


def execute_scale(config: dict, *, output_root: str | Path, call_count: int = 5,
                  windows: int = 2, time_limit_seconds: float = 30,
                  candidates_per_pair: int = 6, total_candidates: int = 240,
                  improvement_rounds: int = 2, call_ids: list[str] | None = None,
                  run_id: str | None = None, runner: Callable | None = None,
                  snapshot: dict | None = None, preflight: dict | None = None,
                   tie_break_mode: str = 'NONE') -> dict:
    """Run original Stage-4 code without replacing any decision logic.

    The optional runner/snapshot injection is for unit tests only. A real CLI
    invocation must always execute full source preflight and the real optimizer.
    """
    start = monotonic()
    if tie_break_mode not in ('NONE','CANONICAL_ETB_BERTH_V1'):
        raise ScaleInputError('Invalid stable tie-break mode')
    bounded_parameters(count=call_count, windows=windows, time_limit_seconds=time_limit_seconds,
                       candidates_per_pair=candidates_per_pair, total_candidates=total_candidates,
                       improvement_rounds=improvement_rounds,scenario=config.get('scenario','V5_3TANK'))
    if snapshot is None:
        snapshot, preflight = preflight_sources(config)
    scope = select_scope(snapshot, call_count, call_ids)
    if call_ids is not None and len(scope) != call_count:
        raise ScaleInputError('call_count must match explicit call_ids length')
    if run_id is None:
        run_id = 'v5-' + uuid.uuid4().hex[:16]
    if not (isinstance(run_id, str) and 4 <= len(run_id) <= 60 and
            all(c.isalnum() and c.isascii() or c in '-_' for c in run_id)):
        raise ScaleInputError('Invalid run ID')
    root = Path(output_root).resolve()
    directory = root / run_id
    if directory.exists():
        raise FileExistsError('Refusing to reuse existing run ID')
    directory.mkdir(parents=True)
    from ..integrated_input.provenance import canonical, digest
    source_fingerprint = digest(canonical(snapshot).encode('utf-8'))
    report = {'run_id': run_id, 'source_fingerprint': source_fingerprint,
              'scope_call_ids': scope, 'excluded_call_count': len(snapshot['calls']) - len(scope),
              'restricted_scope': len(scope) < len(snapshot['calls']),
              'preflight_status': (preflight or {}).get('status', 'INJECTED_TEST_ONLY'),
              'preflight_warnings': (preflight or {}).get('warnings', []),
              'rolling_optimizer_mode': 'INTEGRATED_COST_AWARE_V1',
              'pricing_policy': config['pricing_policy'], 'simulation_only': True,
              'operational_approval': False, 'requested_windows': windows,
              'time_budget_seconds_per_window': time_limit_seconds}
    if snapshot.get('scenario_id')=='V5_BALANCED_154':report['scenario_id']='V5_BALANCED_154'
    if tie_break_mode!='NONE':report['tie_break_mode']=tie_break_mode
    try:
        from .v5_stage1_contract import classify_run, inspect_execution_coverage, SimulationContractError
        # Injected runners are isolated fixtures and are not source preflights.
        coverage = inspect_execution_coverage(snapshot, config, windows) if runner is None else None
        if coverage is not None:
            report['input_coverage_v1'] = coverage
            if coverage['status'] != 'COVERAGE_PRECHECK_PASS':
                report.update(status='INPUT_COVERAGE_BLOCKED', validated_window_count=0,
                              requested_scope_count=len(scope), completed_scope_count=0,
                              all_scope_calls_completed=False, all_requested_windows_validated=False)
                report['execution_outcome_v1'] = classify_run(report, coverage=coverage)
                atomic_json(directory / 'execution_report.json', report)
                return report
        # Source-derived read-only metadata for preview. Never expose contracts or
        # raw source files to browsers; exact numeric fields remain strings.
        if runner is None:
            from .v5_rolling import run_v5_rolling
            runner = run_v5_rolling
        result = runner(snapshot, data_dir=config['data_dir'], policy=config['policy'],
                        pilotage_context=config['pilotage_context'], as_of_at=config['as_of_at'],
                        output_dir=directory, call_ids=scope, windows=windows,
                        time_limit_seconds=time_limit_seconds,
                        pricing_policy=config['pricing_policy'], clock_policy=config['clock_policy'],
                        settlement_records=config['settlement_records'],
                        rolling_optimizer_mode='INTEGRATED_COST_AWARE_V1',
                        max_improvement_rounds=improvement_rounds,
                        max_candidates_per_pair=candidates_per_pair,
                        max_total_candidates=total_candidates,
                         **({'tie_break_mode':tie_break_mode} if tie_break_mode!='NONE' else {}),
                         **({'inventory_policy_version':config['inventory_policy_version']} if config.get('inventory_policy_version') is not None else {}),
                         **({'inventory_risk_policy_version':config['inventory_risk_policy_version']} if config.get('inventory_risk_policy_version') is not None else {}),
                         **({'deferral_policy_version':config['deferral_policy_version'],'max_deferral_probes':config['max_deferral_probes']} if config.get('deferral_policy_version') is not None else {}))
        atomic_json(directory / 'tanks_public.json', [
            {key: t.get(key) for key in ('tank_id', 'cargo_group',
              'min_inventory_bbl', 'initial_inventory_bbl', 'capacity_bbl',
              'max_receipt_rate_bbl_per_hour', 'max_dispatch_rate_bbl_per_hour')}
            for t in snapshot['tanks']])
        atomic_json(directory / 'calls_public.json', [
            {key: c.get(key) for key in ('call_id', 'vessel_id', 'eta_at',
              'cargo_group', 'direction', 'cargo_volume_bbl')}
            for c in snapshot['calls'] if c['call_id'] in scope])
        summary = summarize_result(result, scope, seconds=monotonic()-start)
        report.update(summary)
        try:
            report['execution_outcome_v1'] = classify_run(report, coverage=coverage)
        except SimulationContractError as exc:
            report['execution_outcome_v1'] = {'execution_status': 'INVALID_EVIDENCE',
                                              'completion_status': 'NOT_VERIFIED', 'detail': str(exc)}
        # Only a successful *rolling status* gives validated windows; success of
        # the entire call scope is a separate assertion.
        if len(scope) == len(snapshot['calls']):
            label='full_154_completion_status' if snapshot.get('scenario_id')=='V5_BALANCED_154' else 'full_140_completion_status'
            report[label]='COMPLETED_SYNTHETIC' if summary['all_scope_calls_completed'] else 'INCOMPLETE'
        atomic_json(directory / 'execution_report.json', report)
        return report
    except Exception as exc:
        # Never label an exception as a validated run. Existing checkpoint files,
        # if any, remain for manual inspection and replay.
        report.update(status='EXECUTION_ERROR', error_code=type(exc).__name__,
                      error_message=str(exc), total_elapsed_seconds=monotonic()-start)
        report['execution_outcome_v1'] = {'execution_status': 'EXECUTION_ERROR',
                                          'completion_status': 'NOT_VERIFIED',
                                          'reason_category': type(exc).__name__}
        atomic_json(directory / 'execution_report.json', report)
        return report
