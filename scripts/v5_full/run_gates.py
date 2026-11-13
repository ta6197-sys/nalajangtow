"""Run requested diagnostic gates sequentially and stop on first failure."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.daily_planning.integrated_v3.v5_scale import load_execution_config, BALANCED_GATES
GATES = ('S5', 'S10', 'S20', 'R7', 'R14', 'FULL140')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output-root', type=Path, default=ROOT / 'results' / 'v5_preview_runs')
    parser.add_argument('--through', choices=GATES+('FULL154',), default='S20')
    parser.add_argument('--seconds-per-window', type=float, default=60)
    args = parser.parse_args()
    scenario=load_execution_config(args.config)['scenario']
    gate_order=tuple(BALANCED_GATES) if scenario in ('V5_BALANCED_154','V5_BALANCED_154_EXT_20270109') else GATES
    if args.through not in gate_order:parser.error('Gate incompatible with input profile')
    summary = []
    for gate in gate_order[:gate_order.index(args.through) + 1]:
        cmd = [sys.executable, str(ROOT / 'scripts/v5_full/run_stage.py'),
               '--config', str(args.config), '--output-root', str(args.output_root),
               '--gate', gate, '--seconds-per-window', str(args.seconds_per_window)]
        proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
        # Full stage report remains in each run directory; do not parse fake success.
        entry = {'gate': gate, 'exit_code': proc.returncode,
                 'stdout_tail': proc.stdout[-3500:], 'stderr_tail': proc.stderr[-1500:]}
        summary.append(entry)
        print(json.dumps(entry, ensure_ascii=False, indent=2), flush=True)
        if proc.returncode:
            print(json.dumps({'status': 'STOPPED_AT_GATE', 'gate': gate, 'gates': summary}, ensure_ascii=False))
            return proc.returncode
    print(json.dumps({'status': 'REQUESTED_GATES_COMPLETED', 'gates': [x['gate'] for x in summary]}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
