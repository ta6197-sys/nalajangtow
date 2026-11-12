"""Comparable bounded runs for 6/12/24 ETB-candidates-per-pair caps.

Never infer performance gains from a run where different numbers of windows
were validated. Projection costs from overlapping windows are NOT additive.
"""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.daily_planning.integrated_v3.v5_scale import (
    GATES, execute_scale, load_execution_config, preflight_sources)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--gate', choices=('S5', 'S10', 'S20'), default='S5')
    p.add_argument('--output-root', type=Path, default=ROOT / 'results' / 'v5_preview_runs')
    p.add_argument('--seconds-per-window', type=float, default=60)
    args = p.parse_args()
    config = load_execution_config(args.config)
    snapshot, preflight = preflight_sources(config)
    count, windows = GATES[args.gate]
    comparison = []
    for cap in (6, 12, 24):
        r = execute_scale(config, snapshot=snapshot, preflight=preflight,
                          output_root=args.output_root, call_count=count,
                          windows=windows, time_limit_seconds=args.seconds_per_window,
                          candidates_per_pair=cap, total_candidates=max(240, count * cap * 3),
                          improvement_rounds=2)
        comparison.append({'cap': cap, 'run_id': r['run_id'], 'status': r['status'],
                           'validated_window_count': r.get('validated_window_count'),
                           'elapsed_seconds': r.get('total_elapsed_seconds'),
                           'window_projected_costs_cents_NOT_ADDITIVE': r.get('window_projected_costs_cents_NOT_ADDITIVE'),
                           'cumulative_demurrage_cents': r.get('cumulative_demurrage_cents')})
        if r['status'] != 'REQUESTED_WINDOWS_VALIDATED':
            break
    print(json.dumps({'comparison': comparison, 'same_source_and_policy': True,
                      'cost_comparison_warning': 'Only compare matching window coverage and accounting scope.'},
                     ensure_ascii=False, indent=2))
    return 0 if len(comparison) == 3 and all(x['status'] == 'REQUESTED_WINDOWS_VALIDATED' for x in comparison) else 2


if __name__ == '__main__':
    sys.exit(main())
