#!/usr/bin/env python3
"""Read-only portable Bridge V2 structural attestation (NOT physical replay)."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from src.daily_planning.integrated_v3.v5_simulation_bridge_v2 import verify_bundle_attestation

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('bridge_file',type=Path)
    args=p.parse_args(argv)
    try:
        f=args.bridge_file
        if f.is_symlink() or not f.is_file() or f.stat().st_size > 120_000_000:
            raise ValueError('Missing, linked or oversized Bridge V2 file')
        result=verify_bundle_attestation(json.loads(f.read_text(encoding='utf-8')))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (OSError,TypeError,KeyError,ValueError,json.JSONDecodeError) as exc:
        print(json.dumps({'status':'BLOCKED','error':type(exc).__name__,'detail':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2
if __name__=='__main__':sys.exit(main())
