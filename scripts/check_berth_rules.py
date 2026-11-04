"""CLI to test SK physical constraints without touching PostgreSQL or CP-SAT."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.daily_planning.berth_rules import (check_physical_compatibility,
     load_facilities, load_mapping, validate_mapping)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-facilities", action="store_true")
    parser.add_argument("--validate-mapping", action="store_true")
    parser.add_argument("--facility", help="e.g. SKB#2, SK#4-1")
    parser.add_argument("--draft-m", type=str)
    parser.add_argument("--dwt", type=str)
    parser.add_argument("--loa-m", type=str)
    parser.add_argument("--cargo", type=str)
    args = parser.parse_args()
    profiles = load_facilities()
    if args.list_facilities:
        print("\n".join(profiles))
        return 0
    if args.validate_mapping:
        problems = validate_mapping(load_mapping(),profiles)
        if problems:
            print("MAPPING NOT READY:")
            for p in problems:
                print(" -",p)
            return 2
        print("MAPPING VALID")
        return 0
    if args.facility:
        if args.facility not in profiles:
            parser.error(f"Unknown SK facility: {args.facility}")
        result = check_physical_compatibility(
            {"draft_m":args.draft_m,"dwt":args.dwt,"loa_m":args.loa_m,
             "cargo_type":args.cargo}, profiles[args.facility])
        print(json.dumps(result,indent=2,ensure_ascii=False))
        return 0 if result["status"] == "PASS" else 1
    parser.error("Provide --list-facilities, --validate-mapping, or --facility")


if __name__ == "__main__":
    raise SystemExit(main())
