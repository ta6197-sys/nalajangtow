"""Inspect an existing integrated V5 rolling result without changing it.

Example:
  python scripts/v5_rolling/audit_integrated_run.py results/v5_rolling_runs/<run>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.daily_planning.integrated_v3.v5_rolling_audit import audit_rolling_run


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only V5 rolling checkpoint audit")
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--allow-legacy", action="store_true", help="Allow legacy window runs too")
    args = parser.parse_args(argv)
    result = audit_rolling_run(args.run_directory, require_integrated=not args.allow_legacy)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "STRUCTURAL_CHECKPOINT_CHAIN_VALID" else 1


if __name__ == "__main__":
    sys.exit(main())
