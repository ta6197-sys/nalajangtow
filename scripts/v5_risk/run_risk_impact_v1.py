#!/usr/bin/env python3
"""Run a verified V5-native no-shock gate, then screen three shock kinds.

No optimizer is called for a shocked scenario. This is deterministic fixed-
schedule impact screening, NOT a shock replay or revised cost simulation.
All outputs are new, local and source-bound; original files stay unchanged.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.daily_planning.integrated_v3.v5_scale import load_execution_config, preflight_sources
from src.daily_planning.integrated_v3.v5_run_review import load_checkpoints, load_json
from src.daily_planning.integrated_v3.v5_simulation_bridge_v2 import build_bundle, verify_run_no_shock
from src.daily_planning.integrated_v3.v5_risk_impact_v1 import assess, RiskImpactError


def new_json(folder: Path, filename: str, payload: dict):
    path = folder / filename
    with path.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--run-directory', required=True, type=Path)
    parser.add_argument('--scenario-file', required=True, type=Path)
    parser.add_argument('--output-directory', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        run_dir = args.run_directory
        if run_dir.is_symlink() or not run_dir.is_dir():
            raise RiskImpactError('Run directory unavailable or symlinked')
        run_dir = run_dir.resolve(strict=True)
        scenario_file = args.scenario_file
        if scenario_file.is_symlink() or not scenario_file.is_file() or scenario_file.stat().st_size > 50000:
            raise RiskImpactError('Scenario input missing, linked or oversized')
        scenario = json.loads(scenario_file.read_text(encoding='utf-8'))
        output = args.output_directory.resolve()
        config = load_execution_config(args.config)
        source = Path(config['data_dir']).resolve()
        if output.exists():
            raise RiskImpactError('Output already exists; use a unique new output directory')
        if any(output == path or path in output.parents for path in (source, run_dir)):
            raise RiskImpactError('Output must not be located inside verified source or run')
        manifest, checkpoints = load_checkpoints(run_dir)
        if not checkpoints or len(checkpoints) != manifest['config']['windows']:
            raise RiskImpactError('Complete validated Rolling checkpoint chain required')
        report_file = run_dir / 'execution_report.json'
        if report_file.is_file():
            report = load_json(report_file)
            if report.get('status') != 'REQUESTED_WINDOWS_VALIDATED' or \
               report.get('validated_window_count') != len(checkpoints) or \
               report.get('source_fingerprint') != manifest['config']['source_fingerprint']:
                raise RiskImpactError('Run report does not confirm validated windows')
        snapshot, preflight = preflight_sources(config)
        daylight = Path(config['pilotage_context']['daylight_path']).read_bytes()
        bridge = build_bundle(snapshot, config, manifest, checkpoints, daylight)
        native = verify_run_no_shock(snapshot, config, manifest, checkpoints,
                                     run_directory=run_dir, require_complete=True)
        risk = assess(bridge, scenario, baseline_audit=native)
        risk['source_preflight_status'] = preflight.get('status')
        risk['run_id'] = run_dir.name
        # The full Bridge V2 is deliberately not exported here: it can include
        # contracts and sensitive source records; reuse PR #88 exporter if needed.
        output.mkdir(parents=True, exist_ok=False)
        new_json(output, 'native_no_shock_evidence.json', native)
        new_json(output, 'risk_impact_v1.json', risk)
        print(json.dumps({
            'status': risk['status'], 'run_id': run_dir.name,
            'scenario_id': risk['scenario_id'],
            'shock_event_count': risk['shock_event_count'],
            'affected_call_ids': risk['affected_call_ids'],
            'source_fingerprint': risk['bridge_source_fingerprint'],
            'output_directory': str(output),
            'scenario_replay': 'NOT_RUN',
            'operational_approval': False,
        }, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, ImportError, json.JSONDecodeError) as exc:
        print(json.dumps({'status':'BLOCKED','error':type(exc).__name__,
                          'reason':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
