"""Structural/checksum audit of a persisted Stage-4 V5 rolling run."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.daily_planning.integrated_v3.v5_run_review import InvalidPreviewRun, run_summary, window_view


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, default=ROOT / 'results' / 'v5_preview_runs')
    p.add_argument('--run-id', required=True)
    a = p.parse_args()
    try:
        summary = run_summary(a.root, a.run_id)
        windows = []
        for n in range(1, summary['validated_window_count'] + 1):
            w = window_view(a.root, a.run_id, n)
            windows.append({'window_id': n, 'schedule_rows': len(w['selected_schedule']),
                            'inventory_tanks': list(w['tank_inventory']),
                            'validation': w['validation_results'],
                            'final_domain_proven': w['summary'].get('final_domain_optimality_proven')})
        print(json.dumps({'audit': 'STRUCTURAL_ONLY_NOT_INDEPENDENT_PHYSICAL_REPLAY',
                          'status': 'PASS', 'summary': summary, 'windows': windows},
                         ensure_ascii=False, indent=2))
        return 0
    except (InvalidPreviewRun, ValueError, KeyError, TypeError) as e:
        print(json.dumps({'audit': 'STRUCTURAL_ONLY', 'status': 'FAIL',
                          'reason': str(e)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
