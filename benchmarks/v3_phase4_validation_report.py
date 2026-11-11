"""Recheck a saved V3 optimizer result and emit matching JSON and Markdown audits.

Run with: python -m benchmarks.v3_phase4_validation_report --help
No input or prior report is overwritten.
"""
import argparse
import json
from pathlib import Path
from src.daily_planning.integrated_v3.validation_runner import (
    validate_phase4_result, validate_phase4_rolling, render_markdown)
from src.daily_planning.physical_scheduling.user_policy import build_unbounded_synthetic_policy


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshot", type=Path)
    p.add_argument("result", type=Path)
    p.add_argument("--mode", choices=("single", "rolling"), default="single")
    p.add_argument("--as-of-at", help="Required for single mode")
    p.add_argument("--expected-days", type=int, help="Required for rolling mode")
    p.add_argument("--policy-json", type=Path, help="Exact original optimization policy")
    p.add_argument("--same-tank-policy", choices=("ALLOW", "FORBID"))
    p.add_argument("--settlement-records", type=Path)
    p.add_argument("--commitments", type=Path)
    p.add_argument("--completed-rows", type=Path)
    p.add_argument("--initial-inventory", type=Path)
    p.add_argument("--carried-transfers", type=Path)
    p.add_argument("--tank-commitments", type=Path)
    p.add_argument("--pricing-policy", choices=("EXPLICIT_EVENTS_V1", "CLEAR_WEATHER_BASELINE_V1"))
    p.add_argument("--output-json", type=Path, required=True)
    p.add_argument("--output-md", type=Path, required=True)
    args = p.parse_args(argv)
    if args.output_json.resolve() == args.output_md.resolve():
        p.error("JSON and Markdown outputs must have distinct paths")
    if args.output_json.exists() or args.output_md.exists():
        p.error("Output already exists; use fresh paths")
    snapshot = _read(args.snapshot)
    result = _read(args.result)
    flow = snapshot["fixed_process_flows"]
    if args.policy_json:
        policy = _read(args.policy_json)
    else:
        policy = build_unbounded_synthetic_policy(
            snapshot, start_at=flow["inventory_as_of_at"], end_at=flow["flow_horizon_end_at"])
        policy["cargo_duration_policy"] = "VESSEL_BERTH_RATE_V1"
    records = _read(args.settlement_records) if args.settlement_records else []
    if args.mode == "single":
        if not args.as_of_at:
            p.error("--as-of-at required")
        params = dict(
            commitments=_read(args.commitments) if args.commitments else (),
            completed_rows=_read(args.completed_rows) if args.completed_rows else (),
            initial_inventory=_read(args.initial_inventory) if args.initial_inventory else None,
            carried_transfers=_read(args.carried_transfers) if args.carried_transfers else None,
            tank_commitments=_read(args.tank_commitments) if args.tank_commitments else None)
        audit = validate_phase4_result(
            snapshot, result, policy=policy, as_of_at=args.as_of_at,
            same_tank_policy=args.same_tank_policy, settlement_records=records,
            pricing_policy=args.pricing_policy, **params)
    else:
        if not args.expected_days or args.expected_days < 1:
            p.error("--expected-days required for rolling mode")
        if any((args.commitments, args.completed_rows, args.initial_inventory,
                args.carried_transfers, args.tank_commitments)):
            p.error("Rolling mode derives boundary state; do not pass single-window state")
        audit = validate_phase4_rolling(
            snapshot, result, policy=policy, expected_days=args.expected_days,
            same_tank_policy=args.same_tank_policy, settlement_records=records)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    with args.output_json.open("x", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with args.output_md.open("x", encoding="utf-8") as f:
        f.write(render_markdown(audit))
    print(json.dumps(dict(status=audit["status"], report=str(args.output_json),
                          markdown=str(args.output_md), operational_approval=False)))
    return 0 if audit["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
