#!/usr/bin/env python3
"""Versioned, source-pinned extension of V5 synthetic berth/departure windows.

Only creates NEW files. Refuses unrecognized/locally changed source bytes.
No solver code, source JSON, checkpoints or existing configs are modified.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys

BASE_END = '2026-11-09T00:00:00+09:00'
EXT_END = '2027-01-10T00:00:00+09:00'  # exclusive
START = '2026-11-03T00:00:00+09:00'
POLICY_IN = 'v5_user_policy_20261112.synthetic.json'
PILOT_IN = 'v5_balanced_pilotage_context.json'
EXAMPLE_IN = 'v5_balanced_ext_20270109.example.json'
POLICY_OUT = 'v5_user_policy_extended_20270109.synthetic.json'
PILOT_OUT = 'v5_balanced_pilotage_context_extended_20270109.json'
BASIC_OUT = 'v5_balanced_ext_20270109.policy_extended.example.json'
STAGE2B_OUT = 'v5_balanced_ext_20270109.stage2b_extended.example.json'
BASE_BLOBS = {
    POLICY_IN: '4470798942677e8486de9abe88dd6b8c072e7d84',
    PILOT_IN: 'c9e7c6eadb667fa7171953aa296a892349cde7e2',
}
BERTHS = {f'BERTH-{n:02d}' for n in range(1, 12)}
DEPARTURE_CALLS = {'DEM-0011', 'DEM-0022', 'DEM-0047', 'DEM-0103', 'DEM-0112'}


def _blob(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def _read(path: Path, *, baseline: bool = False) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing or symlinked JSON: ' + str(path))
    raw = path.read_bytes()
    if baseline and _blob(raw) != BASE_BLOBS[path.name]:
        raise ValueError('Unexpected baseline Git blob SHA for ' + str(path))

    def no_duplicates(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('Duplicate JSON key: ' + key)
            out[key] = value
        return out

    payload = json.loads(raw, object_pairs_hook=no_duplicates)
    if not isinstance(payload, dict):
        raise ValueError('Root must be object: ' + str(path))
    return payload


def _iso(value: str) -> datetime:
    d = datetime.fromisoformat(value)
    if d.utcoffset() is None:
        raise ValueError('Timezone-aware value required')
    return d


def _extend_ranges(rows: list, context: str) -> None:
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError('Unexpected number of windows: ' + context)
    row = rows[0]
    if set(row) != {'start_at', 'end_at'} or row['start_at'] != START or row['end_at'] != BASE_END:
        raise ValueError('Unexpected source window, refuse to extend: ' + context)
    row['end_at'] = EXT_END


def extend_source(base: dict, pilotage: dict) -> tuple[dict, dict]:
    """Pure transformation: all original non-window rules are preserved."""
    policy = deepcopy(base)
    pilot = deepcopy(pilotage)
    if policy.get('data_origin') != 'SYNTHETIC_FIXTURE' or policy.get('operational_approval') is not False:
        raise ValueError('Expected simulation-only policy')
    if set(policy.get('berths', {})) != BERTHS:
        raise ValueError('Unexpected berth roster')
    count = 0
    for bid, berth in policy['berths'].items():
        for key in ('pilotage_windows', 'work_windows'):
            _extend_ranges(berth.get(key), f'{bid}.{key}')
            count += 1
    if count != 22:
        raise ValueError('Expected 22 berth windows')
    if set(pilot.get('night_berthing_permissions', {})) != BERTHS:
        raise ValueError('Pilotage permission berth roster differs')
    if set(pilot.get('departure_windows_by_berth_call', {})) != BERTHS:
        raise ValueError('Departure berth roster differs')
    dep_count = 0
    for bid, by_call in pilot['departure_windows_by_berth_call'].items():
        if set(by_call) != DEPARTURE_CALLS:
            raise ValueError('Unexpected departure call IDs: ' + bid)
        for cid, windows in by_call.items():
            _extend_ranges(windows, f'{bid}.{cid}')
            dep_count += 1
    if dep_count != 55:
        raise ValueError('Expected 55 departure windows')
    # Intentional portability fix; resolved against config/ by load_execution_config.
    if not str(pilot.get('daylight_path', '')).endswith('/src/daily_planning/daylight_windows.csv'):
        raise ValueError('Unexpected daylight path')
    pilot['daylight_path'] = '../src/daily_planning/daylight_windows.csv'
    return policy, pilot


def config_examples(baseline: dict) -> tuple[dict, dict]:
    if baseline.get('version') != 'V5_STAGED_EXECUTION_V1' or baseline.get('scenario') != 'V5_BALANCED_154_EXT_20270109':
        raise ValueError('Unexpected extended-source execution example')
    if baseline.get('policy_file') != POLICY_IN or baseline.get('pilotage_context_file') != PILOT_IN:
        raise ValueError('Unexpected original config file references')
    cfg = deepcopy(baseline)
    cfg['policy_file'] = POLICY_OUT
    cfg['pilotage_context_file'] = PILOT_OUT
    stage2b = deepcopy(cfg)
    stage2b['inventory_policy_version'] = 'V5_PHYSICAL_BOUNDS_OPERATING_WARNINGS_V1'
    stage2b['inventory_risk_policy_version'] = 'V5_INVENTORY_RISK_20_80_10_90_V1'
    stage2b['deferral_policy_version'] = 'V5_ROLLING_SAFE_ADMISSION_V1'
    stage2b['max_deferral_probes'] = 8
    return cfg, stage2b


def outputs(repo: Path) -> dict[str, dict]:
    root = repo / 'config'
    base = _read(root / POLICY_IN, baseline=True)
    pilot = _read(root / PILOT_IN, baseline=True)
    example = _read(root / EXAMPLE_IN)
    policy_ext, pilot_ext = extend_source(base, pilot)
    cfg, stage2b = config_examples(example)
    return {POLICY_OUT: policy_ext, PILOT_OUT: pilot_ext, BASIC_OUT: cfg, STAGE2B_OUT: stage2b}


def encoded(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def emit(repo: Path, outdir: Path, *, apply: bool = False, verify: bool = False) -> dict:
    artifacts = outputs(repo)
    out = {'mode': 'VERIFY' if verify else 'APPLY' if apply else 'CHECK',
           'base_end': BASE_END, 'new_exclusive_end': EXT_END, 'files': []}
    if apply:
        outdir.mkdir(parents=True, exist_ok=True)
    for name, obj in artifacts.items():
        content = encoded(obj)
        target = outdir / name
        if target.is_symlink():
            raise ValueError('Refusing symlink target: ' + str(target))
        exists = target.exists()
        if (verify or apply) and exists and target.read_bytes() != content:
            raise ValueError('Existing new-version file differs. Will not overwrite: ' + str(target))
        if verify and not exists:
            raise ValueError('Missing generated output: ' + str(target))
        if apply and not exists:
            with target.open('xb') as f:
                f.write(content)
        out['files'].append({'path': str(target), 'size_bytes': len(content),
                             'sha256': hashlib.sha256(content).hexdigest(),
                             'status': 'MATCH' if exists else 'CREATED' if apply else 'WOULD_CREATE'})
    out['simulated_only'] = True
    out['source_data_unchanged'] = True
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, default=Path.cwd(), help='Docktimize repository root')
    p.add_argument('--output-dir', type=Path, help='Folder to place new JSON files (default: REPO/config)')
    m = p.add_mutually_exclusive_group()
    m.add_argument('--apply', action='store_true', help='Write new versioned JSON files, never overwrite')
    m.add_argument('--verify', action='store_true', help='Read-only comparison with expected generated files')
    a = p.parse_args(argv)
    try:
        repo = a.repo.resolve()
        out = a.output_dir.resolve() if a.output_dir else repo / 'config'
        result = emit(repo, out, apply=a.apply, verify=a.verify)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
