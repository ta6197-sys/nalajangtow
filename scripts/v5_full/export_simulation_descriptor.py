"""Export one structurally validated rolling window; no simulation replay."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.daily_planning.integrated_v3.v5_stage1_contract import export_window_descriptor


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_directory')
    parser.add_argument('window_id', type=int)
    parser.add_argument('output_directory')
    args = parser.parse_args()
    print(export_window_descriptor(args.run_directory, args.window_id, args.output_directory))
