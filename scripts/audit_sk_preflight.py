#!/usr/bin/env python3
"""Run SK mapping/profile preflight without modifying database or solver."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.daily_planning.sk_preflight import (load_call_profiles, audit_calls,
                                              audit_mapping, SKPreflightError)
from src.daily_planning.berth_rules import load_mapping, load_facilities


def main() -> int:
    p = argparse.ArgumentParser(description="Read-only SK mapping/profile audit")
    p.add_argument("--mapping", type=Path, default=ROOT / "config/berth_mapping.json")
    p.add_argument("--facilities", type=Path, default=ROOT / "data/sk_facilities.json")
    p.add_argument("--profiles", type=Path, help="Input file with calls[]; example profiles are not loaded automatically")
    p.add_argument("--call-id", action="append", help="Evaluate one or more call IDs; repeat flag as needed")
    p.add_argument("--out", type=Path, help="Write full JSON report without overwriting a previous report")
    args = p.parse_args()
    try:
        mapping = load_mapping(args.mapping)
        facilities = load_facilities(args.facilities)
        if args.profiles is None:
            result = {"audit_version": 1, "mapping": audit_mapping(mapping, facilities)}
        else:
            profiles = load_call_profiles(args.profiles)
            ids = args.call_id if args.call_id else sorted(profiles)
            result = audit_calls(mapping, facilities, profiles, ids)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
                f.write("\n")
        brief = {"mapping": result["mapping"]}
        if "summary" in result:
            brief["summary"] = result["summary"]
            brief["calls"] = [{"call_id": c["call_id"], "status": c["status"],
                               "eligible_berths": c["eligible_berths"], "reason": c["reason"]}
                              for c in result["calls"][:20]]
            if len(result["calls"]) > 20:
                brief["truncated_display_only"] = len(result["calls"]) - 20
        print(json.dumps(brief, ensure_ascii=False, indent=2))
        ok = result["mapping"]["status"] == "READY"
        if "summary" in result:
            ok = ok and result["summary"]["blocked"] == 0 and result["summary"]["ready"] > 0
        return 0 if ok else 2
    except (ValueError, OSError, json.JSONDecodeError, SKPreflightError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
