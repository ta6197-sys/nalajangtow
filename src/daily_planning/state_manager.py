"""SQLite repository; operational and simulation records have separate keys."""
from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from .validator import KST, STATUSES, integer, list_value, timestamp, time_value


def now():
    return datetime.now(KST).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class StateStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
        INSERT OR IGNORE INTO metadata VALUES ('revision', 0);
        CREATE TABLE IF NOT EXISTS contracts (
          mode TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY (mode,id));
        CREATE TABLE IF NOT EXISTS calls (
          mode TEXT NOT NULL, id TEXT NOT NULL, contract_id TEXT NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY (mode,id), FOREIGN KEY(mode,contract_id) REFERENCES contracts(mode,id));
        CREATE TABLE IF NOT EXISTS operations (
          mode TEXT NOT NULL, call_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY(mode,call_id), FOREIGN KEY(mode,call_id) REFERENCES calls(mode,id));
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, mode TEXT NOT NULL, created_at TEXT NOT NULL,
          status TEXT NOT NULL, result_path TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL, call_id TEXT,
          run_id TEXT, at TEXT NOT NULL, action TEXT NOT NULL, before_json TEXT, after_json TEXT);
        ''')
        self.db.commit()

    def close(self):
        self.db.close()

    def snapshot(self, mode):
        with self.db:
            self.db.execute('BEGIN')
            revision = self.db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0]
            contracts = {r['id']: json.loads(r['payload']) for r in self.db.execute('SELECT * FROM contracts WHERE mode=?', (mode,))}
            calls = {r['id']: json.loads(r['payload']) for r in self.db.execute('SELECT * FROM calls WHERE mode=?', (mode,))}
            states = {r['call_id']: dict(json.loads(r['payload']), _version=r['version'])
                      for r in self.db.execute('SELECT * FROM operations WHERE mode=?', (mode,))}
        return dict(revision=revision, contracts=contracts, calls=calls, states=states)

    def event(self, mode, cid, run_id, action, before, after):
        self.db.execute('INSERT INTO events(mode,call_id,run_id,at,action,before_json,after_json) VALUES(?,?,?,?,?,?,?)',
                        (mode, cid, run_id, now(), action,
                         encode(before) if before is not None else None, encode(after)))

    def _bump(self):
        self.db.execute("UPDATE metadata SET value=value+1 WHERE key='revision'")

    def apply_inputs(self, mode, expected_revision, contracts, calls, updates, run_id):
        """One transaction: any collision rolls back every input/state change."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            revision = self.db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0]
            if revision != expected_revision:
                raise ValueError('State changed while validating; rerun planning')
            for c in contracts:
                self.db.execute('INSERT INTO contracts VALUES(?,?,?)', (mode, c['contract_id'], encode(c)))
                self.event(mode, None, run_id, 'IMPORT_CONTRACT', None, c)
            for c in calls:
                self.db.execute('INSERT INTO calls VALUES(?,?,?,?)', (mode, c['call_id'], c['contract_id'], encode(c)))
                state = dict(call_id=c['call_id'], status='PLANNED', confirmed=False,
                             remaining_work_minutes=c['processing_minutes'], updated_at=now())
                self.db.execute('INSERT INTO operations VALUES(?,?,?,?)', (mode, c['call_id'], 1, encode(state)))
                self.event(mode, c['call_id'], run_id, 'IMPORT_CALL', None, state)
            for before, state in updates:
                cid = state['call_id']
                stored = self.db.execute('SELECT version,payload FROM operations WHERE mode=? AND call_id=?', (mode, cid)).fetchone()
                expected = before.get('_version', 1)
                if stored is None or stored['version'] != expected:
                    raise ValueError(f'Operation changed while validating: {cid}')
                if {k:v for k,v in state.items() if k != '_version'} == {k:v for k,v in before.items() if k != '_version'}:
                    continue
                after = {k: v for k, v in state.items() if k != '_version'}
                self.db.execute('UPDATE operations SET version=version+1,payload=? WHERE mode=? AND call_id=?', (encode(after), mode, cid))
                self.event(mode, cid, run_id, 'EXPLICIT_OPERATION_UPDATE', before, after)
            if contracts or calls or updates:
                self._bump()

    def record_run(self, run_id, mode, status, path, payload):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?,?)',
                            (run_id, mode, now(), status, str(Path(path).resolve()), encode(payload)))

    def confirm(self, run_id, candidate_id, call_ids):
        if not call_ids:
            raise ValueError('Specify at least one --call-id; no implicit whole-plan confirmation')
        run = self.db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if run is None or run['status'] != 'SUCCEEDED':
            raise ValueError('Only a successful stored run can be confirmed')
        result = json.loads(run['payload'])
        if candidate_id not in result['candidate_ids']:
            raise ValueError(f'Unknown candidate_id: {candidate_id}')
        folder = Path(run['result_path'])
        plan = json.loads((folder / f'candidate_{candidate_id}' / 'schedule.json').read_text())
        snapshot = json.loads((folder / 'input_snapshot.json').read_text())
        rows = {r['call_id']: r for r in plan}
        mode = run['mode']
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for cid in dict.fromkeys(call_ids):
                if cid not in rows:
                    raise ValueError(f'Call not in candidate: {cid}')
                current = self.db.execute('SELECT * FROM operations WHERE mode=? AND call_id=?', (mode, cid)).fetchone()
                state = dict(json.loads(current['payload']), _version=current['version'])
                before = deepcopy(state)
                if state.get('confirmed') and state.get('schedule') == rows[cid]:
                    continue
                expected = snapshot['state']['states'][cid]
                if state['_version'] != expected['_version']:
                    raise ValueError(f'Stale candidate: {cid} state changed; replan')
                if state['status'] in ('IN_PROGRESS', 'COMPLETED', 'CANCELLED') or state.get('confirmed'):
                    raise ValueError(f'Cannot replace locked schedule: {cid}')
                state.update(confirmed=True, schedule=rows[cid], confirmed_run_id=run_id,
                             confirmed_candidate_id=candidate_id, updated_at=now())
                # Check against all currently locked work, including other runs.
                for other in self.db.execute('SELECT call_id,payload FROM operations WHERE mode=? AND call_id<>?', (mode, cid)):
                    s = json.loads(other['payload'])
                    r = s.get('schedule')
                    if r and (s.get('confirmed') or s['status'] == 'IN_PROGRESS') and s['status'] not in ('COMPLETED', 'CANCELLED'):
                        if overlap(rows[cid], r):
                            raise ValueError(f'Confirmation overlaps {other["call_id"]}: {cid}')
                after = {k:v for k,v in state.items() if k != '_version'}
                self.db.execute('UPDATE operations SET version=version+1,payload=? WHERE mode=? AND call_id=?', (encode(after), mode, cid))
                self.event(mode, cid, run_id, 'CONFIRM_SELECTED_CALL', before, after)
            self._bump()


def overlap(a, b):
    return a['berth_id'] == b['berth_id'] and max(time_value(a['work_start_at']), time_value(b['work_start_at'])) < min(time_value(a['completion_at']), time_value(b['completion_at']))


def prepare_updates(raw_updates, states, calls, as_of, errors):
    updated, changes, seen = deepcopy(states), [], set()
    permitted = {'call_id', 'status', 'ata', 'berth_id', 'work_start_at', 'completion_at',
                 'remaining_work_minutes', 'weather_pause_intervals', 'expected_version', 'updated_at', '_source'}
    for row_number, raw in enumerate(raw_updates, 2):
        cid = raw.get('call_id')
        try:
            if cid not in calls:
                raise ValueError(f'Unknown call_id: {cid}')
            if cid in seen:
                raise ValueError(f'Duplicate operational update: {cid}')
            seen.add(cid)
            unknown = set(raw) - permitted
            if unknown:
                raise ValueError(f'Unsupported update fields: {sorted(unknown)}')
            old = updated[cid]
            state = deepcopy(old)
            if raw.get('expected_version') not in (None, '') and integer(raw['expected_version'], 'expected_version') != old.get('_version', 1):
                raise ValueError('expected_version conflicts with current DB version')
            status = raw.get('status') or old['status']
            if status not in STATUSES:
                raise ValueError(f'Unknown status: {status}')
            transitions = {'PLANNED': STATUSES, 'WAITING': {'WAITING', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'},
                           'IN_PROGRESS': {'IN_PROGRESS', 'COMPLETED'}, 'COMPLETED': {'COMPLETED'}, 'CANCELLED': {'CANCELLED'}}
            if status not in transitions[old['status']]:
                raise ValueError(f'Invalid status transition: {old["status"]} -> {status}')
            state['status'] = status
            for name in ('ata', 'work_start_at', 'completion_at'):
                if raw.get(name) not in ('', None):
                    state[name] = timestamp(raw[name])
                    if time_value(state[name]) > as_of:
                        raise ValueError(f'{name}: actual event is after planning as_of')
            if raw.get('berth_id') not in ('', None):
                state['berth_id'] = raw['berth_id']
            if raw.get('remaining_work_minutes') not in ('', None):
                state['remaining_work_minutes'] = integer(raw['remaining_work_minutes'], 'remaining_work_minutes')
            if raw.get('weather_pause_intervals') not in (None, ''):
                state['weather_pause_intervals'] = [[timestamp(a), timestamp(b)] for a, b in list_value(raw['weather_pause_intervals'])]
            if state['remaining_work_minutes'] > calls[cid]['processing_minutes']:
                raise ValueError('remaining_work_minutes exceeds total work')
            if old.get('confirmed'):
                schedule = old['schedule']
                for name in ('berth_id', 'work_start_at'):
                    if name in state and state[name] != schedule[name]:
                        raise ValueError(f'{name} conflicts with confirmed schedule; explicit resolution required')
            if old['status'] == 'IN_PROGRESS':
                for name in ('berth_id', 'work_start_at'):
                    if state.get(name) != old.get(name):
                        raise ValueError(f'Cannot relocate or restart in-progress work: {name}')
            if status == 'IN_PROGRESS':
                for name in ('ata', 'berth_id', 'work_start_at', 'remaining_work_minutes'):
                    if state.get(name) in ('', None):
                        raise ValueError(f'IN_PROGRESS requires {name}')
                if state['remaining_work_minutes'] <= 0:
                    raise ValueError('IN_PROGRESS requires positive remaining_work_minutes')
            if status == 'WAITING' and not state.get('ata'):
                raise ValueError('WAITING requires actual ata')
            if status == 'COMPLETED':
                for name in ('work_start_at', 'completion_at', 'berth_id'):
                    if not state.get(name):
                        raise ValueError(f'COMPLETED requires {name}')
                if time_value(state['completion_at']) <= time_value(state['work_start_at']):
                    raise ValueError('completion_at must be after work_start_at')
                if 'remaining_work_minutes' not in raw or integer(raw['remaining_work_minutes'], 'remaining_work_minutes') != 0:
                    raise ValueError('COMPLETED requires explicit remaining_work_minutes=0')
            if status in ('COMPLETED', 'CANCELLED'):
                state['confirmed'] = False
            if time_value(state.get('ata', calls[cid]['available_at'])) > time_value(state.get('work_start_at', '9999-01-01')):
                raise ValueError('work_start_at precedes actual arrival')
            state['updated_at'] = timestamp(raw['updated_at']) if raw.get('updated_at') else as_of.isoformat()
            if time_value(state['updated_at']) > as_of:
                raise ValueError('updated_at is after as_of')
            updated[cid] = state
            changes.append((old, state))
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            errors.append({'input': 'operational_updates', 'id': cid, 'row': row_number,
                           'message': str(exc), 'source': raw.get('_source')})
    return updated, changes
