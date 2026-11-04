#!/usr/bin/env python3
"""Detailed read-only audit for 140 SYNTHETIC vessel profiles vs 8 single berths."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.daily_planning.sk_physical_v3 import (
    _load_source, read_profiles, EXPECTED_MAPPING,
)
from src.daily_planning.berth_rules import check_call_against_virtual_berth


def audit(profiles, mapping, facilities, ids):
    report = []
    reason_occurrences = Counter()
    for cid in ids:
        if cid not in profiles:
            report.append({"call_id": cid, "status": "VESSEL_PROFILE_MISSING",
                           "eligible_berths": [], "rejections": {}})
            continue
        vessel = profiles[cid]
        passes = []
        rejected = {}
        for bid, code in sorted(mapping.items()):
            result = check_call_against_virtual_berth(vessel, bid, mapping, facilities)
            if result["status"] == "PASS":
                passes.append(bid)
            else:
                issues = result["issues"]
                rejected[code] = {"status": result["status"], "issues": issues}
        report.append({
            "call_id": cid, "data_origin": "SYNTHETIC",
            "cargo_type": vessel["cargo_type"], "dwt": vessel["dwt"],
            "loa_m": vessel["loa_m"], "draft_m": vessel["draft_m"], "gt": vessel["gt"],
            "eligible_berths": passes, "status": (
                "SINGLE_BERTH_PASS" if passes else "NO_COMPATIBLE_BERTH_IN_MODEL"
            ), "rejections": rejected if not passes else {},
        })
    blocked = [r for r in report if r["status"] != "SINGLE_BERTH_PASS"]
    for row in blocked:
        for rejected in row["rejections"].values():
            for issue in rejected["issues"]:
                reason_occurrences[issue["code"]] += 1
    return {
        "summary": {
            "data_origin": "SYNTHETIC",
            "scope": "DOCUMENTED_SINGLE_BERTH_PHYSICAL_ONLY",
            "total_calls": len(report), "passed": len(report) - len(blocked),
            "blocked": len(blocked),
            "blocked_call_ids": [r["call_id"] for r in blocked],
            "rejection_reason_occurrences_per_berth": dict(sorted(reason_occurrences.items())),
            "not_modeled": ["MULTI_BERTH_OCCUPANCY", "TIDE_DYNAMIC_UKC", "BERTHING_WEATHER",
                            "CARGO_WEATHER", "TUG_PILOT", "PIPELINE_TANK", "ACTUAL_PORT_APPROVAL"],
        },
        "blocked_calls": blocked,
        "compatibility_matrix": {r["call_id"]: r["eligible_berths"] for r in report},
    }


def main():
    p = argparse.ArgumentParser(description="SYNTHETIC SK 8-single-berth audit; exit 2 on blocked")
    p.add_argument("--profiles", type=Path, default=ROOT / "input/vessel_profiles.sk-v3-independent.json")
    p.add_argument("--call-ids-file", type=Path, default=ROOT / "input/call_ids.txt")
    p.add_argument("--expected-count", type=int, default=140)
    p.add_argument("--out", type=Path, default=ROOT / "results/sk_v3_incompatible_calls.json")
    args = p.parse_args()
    try:
        mapping, facilities = _load_source(
            ROOT / "config/berth_mapping.json", ROOT / "data/sk_facilities.json"
        )
        profiles = read_profiles(args.profiles)
        ids = [s.strip() for s in args.call_ids_file.read_text(encoding="utf-8").splitlines() if s.strip()]
        if len(ids) != args.expected_count or len(set(ids)) != len(ids):
            raise ValueError("Call ID file does not have expected number of distinct IDs")
        if set(ids) != set(profiles):
            raise ValueError(
                f"Call ID set mismatch: missing={sorted(set(ids)-set(profiles))[:12]}, "
                f"extra={sorted(set(profiles)-set(ids))[:12]}"
            )
        result = audit(profiles, mapping, facilities, sorted(ids))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
        print(f"Full per-berth failure reasons: {args.out}")
        return 2 if result["summary"]["blocked"] else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"SK physical input error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
