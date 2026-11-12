"""Stage 4 smoke run against existing TEST-ONLY synthetic fixtures.

This uses unittest.mock to bypass source preflight, so it is **not** a
verified real-input execution. Never label the output as operationally valid.
The integrated scheduling and rolling paths themselves are run, not mocked.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.test_v5_rolling import rolling_fixture, START
from src.daily_planning.integrated_v3.v5_rolling import run_v5_rolling
from src.daily_planning.integrated_v3.v5_rolling_audit import audit_rolling_run


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stage 4 test fixture smoke run")
    parser.add_argument("--windows", type=int, choices=(2, 3), default=3)
    parser.add_argument("--seconds-per-window", type=float, default=30)
    parser.add_argument("--output-root", type=Path, default=ROOT / "results" / "v5_rolling_runs")
    args = parser.parse_args(argv)
    if not (0 < args.seconds_per_window <= 120):
        parser.error("--seconds-per-window must be within (0, 120]")
    args.output_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="stage4-fixture-", dir=args.output_root))
    run_dir = root / "rolling"
    snapshot, policy, pilotage = rolling_fixture()
    # Mirror the existing real integrated test's second berth and connections.
    new_berth = copy.deepcopy(snapshot["berths"][0])
    new_berth["berth_id"] = "B2"
    snapshot["berths"].append(new_berth)
    snapshot["tanks"][0]["connected_berth_ids"].append("B2")
    original_bid = snapshot["berths"][0]["berth_id"]
    policy["berths"]["B2"] = copy.deepcopy(policy["berths"][original_bid])
    pilotage["night_berthing_permissions"]["B2"] = True
    pilotage["departure_windows_by_berth_call"]["B2"] = copy.deepcopy(
        pilotage["departure_windows_by_berth_call"][original_bid]
    )
    kwargs = dict(
        data_dir="ISOLATED_SYNTHETIC_TEST",
        policy=policy,
        pilotage_context=pilotage,
        as_of_at=START,
        output_dir=run_dir,
        windows=args.windows,
        time_limit_seconds=args.seconds_per_window,
        pricing_policy="CLEAR_WEATHER_BASELINE_V1",
        rolling_optimizer_mode="INTEGRATED_COST_AWARE_V1",
    )
    with patch(
        "src.daily_planning.integrated_v3.v5_physical._verified_snapshot",
        side_effect=lambda *a: copy.deepcopy(snapshot),
    ), patch(
        "src.daily_planning.integrated_input.v5.validate_v5",
        return_value={"data_preflight_pass": True, "errors": []},
    ):
        # Checkpoint transition, pause/resume, and final-state fingerprinting.
        first = run_v5_rolling(snapshot, **kwargs, max_new_windows=1)
        if first["status"] == "PAUSED":
            last = run_v5_rolling(snapshot, **kwargs, resume=True)
        else:
            last = first
    audit = audit_rolling_run(run_dir)
    success = (
        first["status"] == "PAUSED"
        and last["status"] == "REQUESTED_WINDOWS_VALIDATED"
        and last["validated_window_count"] == args.windows
        and audit["status"] == "STRUCTURAL_CHECKPOINT_CHAIN_VALID"
    )
    report = {
        "status": "TEST_FIXTURE_SMOKE_PASSED" if success else "TEST_FIXTURE_SMOKE_FAILED",
        "data_provenance": "MOCKED_SYNTHETIC_FIXTURE_NOT_VERIFIED_SOURCE_DATA",
        "operational_approval": False,
        "run_dir": str(run_dir),
        "first_status": first["status"],
        "final_status": last["status"],
        "final_result": last,
        "checkpoint_audit": audit,
    }
    path = root / "smoke_summary.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report_path": str(path),
                      "run_dir": str(run_dir), "windows": args.windows,
                      "checkpoint_audit_status": audit["status"],
                      "first_status": first["status"], "last_status": last["status"]},
                     ensure_ascii=False, indent=2))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
