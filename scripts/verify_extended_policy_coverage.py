#!/usr/bin/env python3
"""Run repository V5 coverage prechecks against versioned extended synthetic config.

Source/declaration/daylight coverage only. This does NOT solve any vessel schedule.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True, type=Path)
    p.add_argument('--windows', nargs='+', type=int, default=[2,3,60,65])
    args = p.parse_args(argv)
    try:
        from src.daily_planning.integrated_v3.v5_scale import load_execution_config, preflight_sources
        from src.daily_planning.integrated_v3.v5_stage1_contract import inspect_execution_coverage
        config = load_execution_config(args.config)
        snap, source_report = preflight_sources(config)
        runs = []
        for count in args.windows:
            if not 1 <= count <= 65:
                raise ValueError('Window count must be 1..65')
            result = inspect_execution_coverage(snap,config,count)
            runs.append({'windows':count,'status':result['status'],
                         'required_until_utc':result.get('required_until_utc'),
                         'blockers':result.get('blockers',[]),
                         'warnings':result.get('warnings',[])})
        good = all(r['status']=='COVERAGE_PRECHECK_PASS' for r in runs)
        print(json.dumps({'status':'COVERAGE_PRECHECK_PASS' if good else 'COVERAGE_BLOCKED',
                          'scenario':config['scenario'],'source_preflight':source_report['status'],
                          'windows':runs,'optimization':'NOT_RUN',
                          'physical_feasibility':'NOT_PROVEN',
                          'operational_approval':False},indent=2,ensure_ascii=False,sort_keys=True))
        return 0 if good else 2
    except (OSError,ImportError,ValueError,TypeError,KeyError) as exc:
        print(json.dumps({'status':'INVALID_INPUT','error':type(exc).__name__,
                          'reason':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
