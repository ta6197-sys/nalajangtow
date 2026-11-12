"""Read-only, source-traceable view of V5 Stage-4 rolling checkpoints.

This checks persisted fingerprints and cross-window structural continuity. It
DOES NOT replace the real optimizer's physical, inventory and contract replay;
only the Stage-4 run/resume path can establish those validations.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

MAX_JSON_BYTES = 60_000_000
MAX_WINDOWS = 65
RUN_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{3,59}$')


class InvalidPreviewRun(ValueError):
    pass


def fingerprint(value: Any) -> str:
    from hashlib import sha256
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def load_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise InvalidPreviewRun('Missing, symlinked or oversized preview data')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidPreviewRun('Corrupted JSON result') from exc


def resolve_run(root: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise InvalidPreviewRun('Invalid run ID')
    base = Path(root).resolve()
    directory = base / run_id
    if directory.is_symlink() or not directory.is_dir() or directory.resolve().parent != base:
        raise InvalidPreviewRun('Unknown run ID')
    return directory


def load_checkpoints(directory: Path) -> tuple[dict, list[dict]]:
    """Reject a false continuity/proof claim. Allow partial validated runs."""
    manifest = load_json(directory / 'manifest.json')
    if not isinstance(manifest, dict) or not isinstance(manifest.get('config'), dict):
        raise InvalidPreviewRun('Invalid manifest')
    if manifest.get('run_fingerprint') != fingerprint(manifest['config']):
        raise InvalidPreviewRun('Rolling manifest fingerprint mismatch')
    config = manifest['config']
    if config.get('rolling_optimizer_mode') != 'INTEGRATED_COST_AWARE_V1':
        raise InvalidPreviewRun('Not an integrated V5 rolling run')
    if config.get('objective_mode') != 'MIN_TOTAL_DEMURRAGE' or config.get('inventory_constraint_mode') != 'EXACT_EVENT_BALANCE_V1':
        raise InvalidPreviewRun('Unexpected optimization configuration')
    paths = sorted(directory.glob('window_*.json'))
    if len(paths) > MAX_WINDOWS:
        raise InvalidPreviewRun('Too many windows')
    saved = []
    from datetime import datetime
    start = datetime.fromisoformat(config['start_at'])
    prior_next_fp = None
    for i, path in enumerate(paths, 1):
        if path.name != f'window_{i:03d}.json':
            raise InvalidPreviewRun('Noncontiguous window sequence')
        data = load_json(path)
        if not isinstance(data, dict) or not isinstance(data.get('summary'), dict):
            raise InvalidPreviewRun('Corrupted window structure')
        body = dict(data)
        got = body.pop('checkpoint_fingerprint', None)
        if got != fingerprint(body):
            raise InvalidPreviewRun('Window checkpoint fingerprint mismatch')
        at = start + timedelta(hours=24 * (i - 1))
        if data.get('window_id') != i or data.get('as_of_at') != at.isoformat():
            raise InvalidPreviewRun('Wrong rolling window boundary')
        if data.get('planning_horizon_end') != (at + timedelta(hours=72)).isoformat():
            raise InvalidPreviewRun('Wrong planning horizon')
        if data.get('run_fingerprint') != manifest['run_fingerprint']:
            raise InvalidPreviewRun('Window belongs to a different run')
        if data.get('optimization_configuration') != config:
            raise InvalidPreviewRun('Window optimizer settings changed')
        if data.get('next_state_fingerprint') != fingerprint(data.get('next_state')):
            raise InvalidPreviewRun('Transition state fingerprint mismatch')
        if prior_next_fp is not None and data.get('prior_state_fingerprint') != prior_next_fp:
            raise InvalidPreviewRun('Rolling state continuity mismatch')
        prior_next_fp = data['next_state_fingerprint']
        if (data.get('carried_inventory') != data['next_state'].get('inventory') or
                data.get('cargo_progress') != data['next_state'].get('cargo_progress')):
            raise InvalidPreviewRun('Carryover data does not match saved next state')
        vals = data.get('validation_results') or {}
        rows = data.get('selected_schedule') or []
        expected_physical = 'VALID' if rows else 'NO_WORK'
        expected_settlement = 'SETTLED' if rows else 'NO_WORK'
        if (vals.get('physical') != expected_physical or vals.get('inventory') != 'VALID' or
                vals.get('settlement') != expected_settlement or vals.get('carryover') != 'VALID'):
            raise InvalidPreviewRun('Unvalidated result must not be presented as valid')
        if data['summary'].get('window_id') != i or data['summary'].get('as_of_at') != at.isoformat():
            raise InvalidPreviewRun('Window summary mismatch')
        candidate = data.get('candidate')
        if rows and (not isinstance(candidate, dict) or candidate.get('status') != 'VALIDATED'):
            raise InvalidPreviewRun('Selected schedule lacks a validated incumbent')
        if candidate:
            opt = candidate.get('optimizer_result') or {}
            final = opt.get('final_candidate_domain') or {}
            proven = data['summary'].get('final_domain_optimality_proven')
            if proven is True and (not final.get('optimality_proven') or opt.get('optimization_status') != 'OPTIMAL'):
                raise InvalidPreviewRun('False final-domain optimality claim')
            if proven is False and final.get('optimality_proven') is True:
                raise InvalidPreviewRun('Conflicting final-domain proof')
            if candidate.get('total_demurrage_cents') != data['summary'].get('window_demurrage_cents'):
                raise InvalidPreviewRun('Window cost inconsistency')
            if opt.get('total_projected_demurrage_cents') != candidate.get('total_demurrage_cents'):
                raise InvalidPreviewRun('Optimizer/contract cost inconsistency')
        saved.append(data)
    return manifest, saved


def _read_optional(directory: Path, filename: str, fallback: Any) -> Any:
    path = directory / filename
    return load_json(path) if path.exists() else fallback


def run_summary(root: Path, run_id: str) -> dict:
    directory = resolve_run(root, run_id)
    manifest, windows = load_checkpoints(directory)
    report = _read_optional(directory, 'execution_report.json', {})
    if report and report.get('run_id') != run_id:
        raise InvalidPreviewRun('Run report ID mismatch')
    if report and report.get('source_fingerprint') != manifest['config'].get('source_fingerprint'):
        raise InvalidPreviewRun('Run/report source fingerprint mismatch')
    return {'run_id': run_id, 'status': report.get('status', 'CHECKPOINTS_ONLY'),
            'validated_window_count': len(windows), 'requested_windows': manifest['config'].get('windows'),
            'full_140_completion_status': report.get('full_140_completion_status'),
            'all_scope_calls_completed': report.get('all_scope_calls_completed'),
            'requested_scope_count': report.get('requested_scope_count', len(manifest['config'].get('scope', []))),
            'cumulative_demurrage_cents': report.get('cumulative_demurrage_cents'),
            'unresolved_accounting_call_ids': report.get('unresolved_accounting_call_ids', []),
            'source_fingerprint': manifest['config'].get('source_fingerprint'),
            'rolling_run_fingerprint': manifest['run_fingerprint'],
            'simulation_only': True, 'operational_approval': False,
            'full_period_global_optimality_proven': False,
            'windows': [{'window_id': x['window_id'], 'as_of_at': x['as_of_at'],
                         'cost_cents': x['summary'].get('window_demurrage_cents'),
                         'selected_call_count': len(x['summary'].get('selected_call_ids', [])),
                         'final_domain_optimality_proven': x['summary'].get('final_domain_optimality_proven'),
                         'solver_status': x['summary'].get('solver_status')}
                        for x in windows],
            'review_scope': 'STRUCTURAL_CHECKPOINT_AUDIT_NOT_INDEPENDENT_PHYSICAL_REPLAY'}


def window_view(root: Path, run_id: str, number: int) -> dict:
    directory = resolve_run(root, run_id)
    _, windows = load_checkpoints(directory)
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= len(windows):
        raise InvalidPreviewRun('Unknown window ID')
    data = windows[number - 1]
    candidate = data.get('candidate') or {}
    optimizer = candidate.get('optimizer_result') or {}
    tank = candidate.get('tank_result') or {}
    breakdown = candidate.get('vessel_demurrage_breakdown') or {}
    raw_rows = data.get('selected_schedule') or []
    # Never expose arbitrary optimizer option caches/source data in the browser.
    phase = ('call_id', 'berth_id', 'pob_at', 'berthing_start_at', 'etb_at',
             'hose_connection_start_at', 'work_start_at', 'work_end_at',
             'unberthing_start_at', 'etu_at', 'physical_clearance_at',
             'switching_start_at', 'switching_end_at', 'work_intervals',
             'hose_connection_end_at', 'hose_disconnect_end_at',
             'departure_pob_at', 'pilot_boarding_at',
             'phase_policy_version', 'direction_seconds', 'switching_rule_seconds')
    rows = [{k: r[k] for k in phase if k in r} for r in raw_rows]
    return {'run_id': run_id, 'window_id': number, 'as_of_at': data['as_of_at'],
            'planning_horizon_end': data['planning_horizon_end'],
            'summary': data['summary'], 'selected_schedule': rows,
            'tank_inventory': tank.get('inventories', {}),
            'tank_extrema': tank.get('inventory_extrema', {}),
            'tank_boundaries': _read_optional(directory, 'tanks_public.json', []),
            'call_metadata': _read_optional(directory, 'calls_public.json', []),
            'vessel_demurrage_breakdown': {k: {'demurrage_cents': v.get('demurrage_cents'),
                                              'status': v.get('status')} for k, v in breakdown.items()},
            'candidate_improvement_history': optimizer.get('cost_improvement_history', []),
            'best_validated_incumbent': {k: v for k, v in (optimizer.get('best_validated_incumbent') or {}).items() if k != 'selected_schedule'},
            'final_candidate_domain': optimizer.get('final_candidate_domain'),
            'cargo_progress': data.get('cargo_progress', {}),
            'carried_inventory': data.get('carried_inventory', {}),
            'lock_decisions': data.get('lock_state', {}),
            'validation_results': data.get('validation_results', {}),
            'contract_accounting_scope': 'OVERLAPPING_WINDOW_COSTS_ARE_NOT_CUMULATIVE',
            'simulation_only': True, 'operational_approval': False,
            'review_scope': 'STRUCTURAL_CHECKPOINT_AUDIT_NOT_INDEPENDENT_PHYSICAL_REPLAY'}


def list_runs(root: Path, limit: int = 30) -> dict:
    base = Path(root).resolve()
    if not base.is_dir():
        return {'runs': [], 'configured': False}
    runs = []
    for p in sorted(base.iterdir(), key=lambda x: x.name, reverse=True):
        if len(runs) >= limit:
            break
        if p.is_symlink() or not p.is_dir() or not RUN_ID.fullmatch(p.name):
            continue
        try:
            runs.append(run_summary(base, p.name))
        except (InvalidPreviewRun, ValueError, TypeError, KeyError) as exc:
            runs.append({'run_id': p.name, 'status': 'INVALID_CHECKPOINT', 'error': str(exc)})
    return {'runs': runs, 'configured': True}
