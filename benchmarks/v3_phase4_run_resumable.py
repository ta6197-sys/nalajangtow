"""Run a reproducible 58-day synthetic V3 costed simulation with daily checkpoints.

Example:
  python -m benchmarks.v3_phase4_run_resumable SNAPSHOT.json \
    --output-dir results/v3_phase4/rolling_resumable_v1 \
    --days 58 --seconds-per-strategy 60 --same-tank-policy ALLOW

Run the identical command with --resume after interruption or a fixed solver update.
"""
import argparse
import json
from pathlib import Path

from src.daily_planning.integrated_v3.resumable_rolling import run_resumable_rolling
from src.daily_planning.physical_scheduling.user_policy import build_unbounded_synthetic_policy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--days", type=int, default=58)
    parser.add_argument("--seconds-per-strategy", type=float, default=60)
    parser.add_argument("--same-tank-policy", choices=("ALLOW", "FORBID"))
    parser.add_argument("--phase-release-policy", choices=("ALL_OPTIONS_V1", "LOCKED_ONLY_V1"),
                        default="ALL_OPTIONS_V1")
    parser.add_argument("--no-inventory-relaxation", action="store_true")
    parser.add_argument("--no-reuse-physical-model", action="store_true")
    parser.add_argument("--capacity-bound", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-new-days", type=int)
    parser.add_argument("--policy-json", type=Path)
    parser.add_argument("--allow-partial-call-coverage", action="store_true")
    args = parser.parse_args(argv)
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    if args.policy_json is not None:
        policy = json.loads(args.policy_json.read_text(encoding="utf-8"))
    else:
        flows = snapshot["fixed_process_flows"]
        policy = build_unbounded_synthetic_policy(
            snapshot, start_at=flows["inventory_as_of_at"],
            end_at=flows["flow_horizon_end_at"])
        policy["cargo_duration_policy"] = "VESSEL_BERTH_RATE_V1"
    result = run_resumable_rolling(
        snapshot, policy=policy, as_of_at=snapshot["fixed_process_flows"]["inventory_as_of_at"],
        output_dir=args.output_dir, days=args.days,
        seconds_per_strategy=args.seconds_per_strategy,
        same_tank_policy=args.same_tank_policy, resume=args.resume,
        max_new_days=args.max_new_days,
        inventory_relaxation=not args.no_inventory_relaxation,
        reuse_physical_model=not args.no_reuse_physical_model,
        phase_release_policy=args.phase_release_policy,
        capacity_bound_policy='V3_SINGLE_TANK_CAPACITY_BOUND_V1' if args.capacity_bound else 'DISABLED',
        require_all_calls=not args.allow_partial_call_coverage)
    print(json.dumps({k: result.get(k) for k in (
        "status", "validated_days", "requested_days", "covered_call_count",
        "total_call_count", "next_day", "blocker")}, ensure_ascii=False))
    print("Audit files:", args.output_dir / "summary.json")
    return 0 if result["status"] == "REQUESTED_WINDOWS_VALIDATED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
