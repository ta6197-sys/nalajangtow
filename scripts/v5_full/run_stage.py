"""Verified, staged real-input V5 execution. Does not fabricate flow/policy data."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.daily_planning.integrated_v3.v5_scale import (
    GATES, ScaleInputError, execute_scale, load_execution_config, preflight_sources)


def main(argv=None):
    p = argparse.ArgumentParser(description='Execute one bounded Stage-4 V5 scale gate.')
    p.add_argument('--config', required=True, type=Path, help='Administrator-provided V5 input/policy configuration')
    p.add_argument('--gate', choices=tuple(GATES), default='S5')
    p.add_argument('--output-root', type=Path, default=ROOT / 'results' / 'v5_preview_runs')
    p.add_argument('--seconds-per-window', type=float, default=60)
    p.add_argument('--candidates-per-pair', type=int, default=6)
    p.add_argument('--total-candidates', type=int, default=240)
    p.add_argument('--improvement-rounds', type=int, default=2)
    p.add_argument('--tie-break-mode', choices=['NONE','CANONICAL_ETB_BERTH_V1'], default='NONE')
    p.add_argument('--preflight-only', action='store_true')
    p.add_argument('--call-ids-file', type=Path, help='JSON list of explicit IDs; length must match gate count')
    args = p.parse_args(argv)
    try:
        config = load_execution_config(args.config)
        snapshot, preflight = preflight_sources(config)
        if args.preflight_only:
            print(json.dumps({'preflight_status': preflight.get('status'),
                              'data_preflight_pass': preflight['data_preflight_pass'],
                              'warnings': preflight.get('warnings', []),
                              'call_count': len(snapshot['calls']), 'berth_count': len(snapshot['berths']),
                              'tank_count': len(snapshot['tanks']),
                              'pricing_policy': config['pricing_policy']}, ensure_ascii=False, indent=2))
            return 0
        count, windows = GATES[args.gate]
        call_ids = None
        if args.call_ids_file:
            call_ids = json.loads(args.call_ids_file.read_text(encoding='utf-8'))
        report = execute_scale(config, output_root=args.output_root, call_count=count,
                               windows=windows, time_limit_seconds=args.seconds_per_window,
                               candidates_per_pair=args.candidates_per_pair,
                               total_candidates=args.total_candidates,
                               improvement_rounds=args.improvement_rounds,
                               call_ids=call_ids, snapshot=snapshot, preflight=preflight,
                               **({'tie_break_mode':args.tie_break_mode} if args.tie_break_mode!='NONE' else {}))
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        if report['status'] != 'REQUESTED_WINDOWS_VALIDATED':
            return 2
        if args.gate == 'FULL140' and not report.get('all_scope_calls_completed'):
            return 3
        return 0
    except (ScaleInputError, ValueError, KeyError, OSError, ImportError) as exc:
        print(json.dumps({'status': 'INVALID_INPUT', 'error': type(exc).__name__,
                          'message': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
