"""Isolated local V5 preview routes; no legacy DB writes or shell commands.

The server owns the allowlisted scenario configuration. Browser-supplied paths,
contracts, Python commands and arbitrary solver options are never accepted.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from urllib.parse import parse_qs

from .v5_run_review import InvalidPreviewRun, list_runs, run_summary, window_view

_ROOT = Path(os.environ.get('DOCKTIMIZE_V5_PREVIEW_ROOT', 'results/v5_preview_runs'))
_CONFIG = os.environ.get('DOCKTIMIZE_V5_PREVIEW_CONFIG', '')
_SINGLETON = None
_SINGLETON_LOCK = threading.Lock()


class PreviewManager:
    def __init__(self, root: Path, registry_path: str | None = None):
        self.root = Path(root).resolve()
        self.registry_path = registry_path
        self.lock = threading.Lock()
        self.active_run_id = None
        self.jobs = {}

    def _registry(self):
        if not self.registry_path:
            return {}
        path = Path(self.registry_path)
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding='utf-8'))
        entries = raw.get('scenarios', {})
        if not isinstance(entries, dict):
            raise ValueError('Invalid administrator scenario registry')
        return entries

    def scenario_names(self):
        return sorted(k for k in self._registry() if k.isidentifier() and 1 <= len(k) <= 64)

    def start(self, body: dict) -> tuple[int, dict]:
        if not isinstance(body, dict) or set(body) - {'scenario', 'vessels', 'windows'}:
            return 400, {'error': 'Only scenario, vessels and windows are accepted'}
        scenario = body.get('scenario')
        vessels = body.get('vessels')
        windows = body.get('windows')
        if not isinstance(scenario, str) or scenario not in self._registry():
            return 400, {'error': 'No verified administrator-configured scenario is available'}
        if type(vessels) is not int or vessels not in (3, 5):
            return 400, {'error': 'Web preview allows only 3 or 5 vessels'}
        if type(windows) is not int or windows not in (2, 3):
            return 400, {'error': 'Web preview allows only 2 or 3 windows'}
        entry = self._registry()[scenario]
        if not isinstance(entry, dict) or not isinstance(entry.get('execution_config'), str):
            return 400, {'error': 'Administrator scenario is invalid'}
        # A controlled registry file supplies all actual input/policy/clock paths.
        config_file = Path(entry['execution_config'])
        if not config_file.is_absolute():
            config_file = Path(self.registry_path).resolve().parent / config_file
        with self.lock:
            if self.active_run_id:
                return 409, {'error': 'Another V5 preview optimization is already running', 'run_id': self.active_run_id}
            run_id = 'v5-' + uuid.uuid4().hex[:16]
            self.active_run_id = run_id
            self.jobs[run_id] = {'run_id': run_id, 'status': 'RUNNING', 'simulation_only': True}
            worker = threading.Thread(target=self._worker, args=(run_id, config_file, vessels, windows),
                                      name='v5-preview-' + run_id, daemon=True)
            worker.start()
        return 202, self.jobs[run_id].copy()

    def _worker(self, run_id: str, config_file: Path, vessels: int, windows: int):
        try:
            from .v5_scale import execute_scale, load_execution_config
            config = load_execution_config(config_file)
            result = execute_scale(config, output_root=self.root,
                                   run_id=run_id, call_count=vessels, windows=windows,
                                   time_limit_seconds=30, candidates_per_pair=6,
                                   total_candidates=240, improvement_rounds=2)
            status = 'COMPLETED' if result.get('status') == 'REQUESTED_WINDOWS_VALIDATED' else 'FAILED'
            payload = {'run_id': run_id, 'status': status, 'validated_window_count': result.get('validated_window_count', 0),
                       'reason': result.get('stopping_reason') or result.get('error_code'), 'simulation_only': True}
        except Exception as exc:
            payload = {'run_id': run_id, 'status': 'FAILED', 'reason': type(exc).__name__ + ': ' + str(exc),
                       'simulation_only': True}
        with self.lock:
            self.jobs[run_id] = payload
            self.active_run_id = None

    def job(self, run_id: str):
        with self.lock:
            info = self.jobs.get(run_id)
            return info.copy() if info else None


def manager():
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = PreviewManager(_ROOT, _CONFIG)
        return _SINGLETON


def dispatch_get(path: str, query: str = '') -> tuple[int, dict]:
    api = manager()
    parts = path.strip('/').split('/')
    try:
        if path == '/api/v5-preview/scenarios':
            return 200, {'scenarios': api.scenario_names(), 'execution_enabled': bool(api.scenario_names())}
        if path == '/api/v5-preview/runs':
            result = list_runs(api.root)
            with api.lock:
                result['active_run_id'] = api.active_run_id
            return 200, result
        if len(parts) == 4 and parts[0:3] == ['api', 'v5-preview', 'jobs']:
            state = api.job(parts[3])
            if state is not None:
                return 200, state
            try:
                summary = run_summary(api.root, parts[3])
                return 200, {'run_id': parts[3], 'status': 'COMPLETED' if summary['status'] == 'REQUESTED_WINDOWS_VALIDATED' else 'FAILED'}
            except InvalidPreviewRun:
                return 404, {'error': 'Unknown preview job'}
        if len(parts) == 4 and parts[0:3] == ['api', 'v5-preview', 'runs']:
            return 200, run_summary(api.root, parts[3])
        if len(parts) == 6 and parts[0:3] == ['api', 'v5-preview', 'runs'] and parts[4] == 'windows':
            if not parts[5].isdigit():
                return 400, {'error': 'Window ID must be numeric'}
            return 200, window_view(api.root, parts[3], int(parts[5]))
        return 404, {'error': 'Unknown V5 preview endpoint'}
    except InvalidPreviewRun as exc:
        return 404, {'error': str(exc)}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return 400, {'error': 'Invalid preview request: ' + str(exc)}


def dispatch_start(body: dict) -> tuple[int, dict]:
    try:
        return manager().start(body)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return 400, {'error': 'Invalid preview configuration: ' + str(exc)}
