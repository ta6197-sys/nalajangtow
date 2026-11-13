#!/usr/bin/env python3
"""Export source-bound V5 Bridge V2 and optional native NO_SHOCK parity.

Example:
  python scripts/v5_full/export_simulation_bridge_v2.py \
      --config config/v5_balanced_ext_20270109.s20_pilotage_gap.example.json \
      --run-directory results/v5_study/v5-0123456789abcdef \
      --output-directory results/v5_bridge_study \
      --verify-no-shock

The CLI never creates a V5 optimization window, modifies input/checkpoints,
updates a database, or overwrites a previous Bridge export.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from src.daily_planning.integrated_v3.v5_scale import load_execution_config,preflight_sources
from src.daily_planning.integrated_v3.v5_run_review import load_checkpoints
from src.daily_planning.integrated_v3.v5_simulation_bridge_v2 import (
    build_bundle,verify_run_no_shock,BridgeError)


def save_new(directory: Path, name: str, data: dict):
    with (directory/name).open('x',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)
        f.write('\n')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True,type=Path)
    p.add_argument('--run-directory',required=True,type=Path)
    p.add_argument('--output-directory',required=True,type=Path)
    p.add_argument('--allow-partial',action='store_true',help='Export validated prefix; cannot certify full no-shock baseline')
    p.add_argument('--verify-no-shock',action='store_true',help='Replay exact phases, inventory, settlement and complete rolling checkpoint chain')
    args=p.parse_args(argv)
    try:
        run_dir=args.run_directory.resolve(strict=True)
        output=args.output_directory.resolve()
        config=load_execution_config(args.config)
        source_dir=Path(config['data_dir']).resolve()
        if (output==run_dir or run_dir in output.parents or output==source_dir or source_dir in output.parents):
            raise BridgeError('Output must not be inside original run or input directory')
        if output.exists():
            raise FileExistsError('Refusing to reuse output directory: '+str(output))
        manifest,checkpoints=load_checkpoints(run_dir)
        if not checkpoints:
            raise BridgeError('No validated checkpoints available')
        expected=manifest['config']['windows']
        if len(checkpoints)!=expected and not args.allow_partial:
            raise BridgeError('Incomplete requested windows: '+str(len(checkpoints))+'/'+str(expected))
        snapshot,preflight=preflight_sources(config)
        daylight=Path(config['pilotage_context']['daylight_path']).read_bytes()
        bundle=build_bundle(snapshot,config,manifest,checkpoints,daylight)
        if args.verify_no_shock:
            try:
                audit=verify_run_no_shock(snapshot,config,manifest,checkpoints,
                          run_directory=run_dir,require_complete=not args.allow_partial)
            except (Exception) as exc:
                # Never create a false PASS when native validation fails.
                audit={'audit_version':'V5_NATIVE_NO_SHOCK_PARITY_V1','status':'BLOCKED',
                       'exception':type(exc).__name__,'reason':str(exc),
                       'simulation_only':True,'operational_approval':False}
        else:
            audit={'audit_version':'V5_NATIVE_NO_SHOCK_PARITY_V1','status':'NOT_RUN',
                   'reason':'--verify-no-shock not requested','simulation_only':True,
                   'operational_approval':False}
        bundle['verification_reference']={'audit_file':'no_shock_parity_v1.json',
                     'audit_status':audit['status'],
                     'independent_shock_engine':'NOT_IMPLEMENTED'}
        output.mkdir(parents=True,exist_ok=False)
        save_new(output,'bridge_v2.json',bundle)
        save_new(output,'no_shock_parity_v1.json',audit)
        print(json.dumps({'bridge_status':'EXPORTED','validated_windows':len(checkpoints),
             'requested_windows':expected,'audit_status':audit['status'],
             'output_directory':str(output),'source_preflight':preflight.get('status'),
             'simulation_only':True,'operational_approval':False},ensure_ascii=False,indent=2))
        if args.verify_no_shock and audit['status']!='NATIVE_NO_SHOCK_BASELINE_PARITY_PASS':
            return 2
        return 0
    except (OSError,ValueError,TypeError,KeyError,ImportError,json.JSONDecodeError) as exc:
        print(json.dumps({'status':'BLOCKED','error':type(exc).__name__,'detail':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
