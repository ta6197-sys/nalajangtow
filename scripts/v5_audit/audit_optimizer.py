#!/usr/bin/env python3
"""No-DB V5 S5 optimization audit. Runs real source-verified Stage 4 unchanged.

Only S5 is supported here. This CLI never updates source input, PostgreSQL,
existing optimizer code, or a saved checkpoint. It may create a NEW run folder
and an optional report file explicitly selected by the caller.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.daily_planning.integrated_v3.v5_optimizer_evidence import (  # noqa: E402
    inspect_s5_evidence, source_evidence,
)
from src.daily_planning.integrated_v3.v5_run_review import (  # noqa: E402
    load_checkpoints, load_json, resolve_run,
)
from src.daily_planning.integrated_v3.v5_rolling_audit import (  # noqa: E402
    audit_rolling_run,
)
from src.daily_planning.integrated_v3.v5_scale import (  # noqa: E402
    ScaleInputError, execute_scale, load_execution_config, preflight_sources,
)
from src.daily_planning.integrated_input.provenance import canonical, digest  # noqa: E402


def _output(report: dict, destination: Path | None) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    if destination is not None:
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        # No accidental overwrite of prior results.
        with destination.open("x", encoding="utf-8") as fp:
            json.dump(report, fp, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            fp.write("\n")


def _run_replay(config_path: Path, output_root: Path, run_id: str, timeout: int) -> dict:
    cmd = [sys.executable, str(ROOT / "scripts/v5_full/verify_resume.py"),
           "--config", str(config_path), "--root", str(output_root),
           "--run-id", run_id]
    try:
        cp = subprocess.run(cmd, text=True, capture_output=True, check=False,
                            timeout=timeout, cwd=ROOT)
        # Bounded output for a diagnostic; do not copy entire inputs into reports.
        try:
            payload = json.loads(cp.stdout)
        except json.JSONDecodeError:
            payload = {"status": "UNPARSEABLE_REPLAY_RESULT"}
        success = (cp.returncode == 0 and payload.get("independent_replay") == "PASS"
                   and payload.get("status") == "REQUESTED_WINDOWS_VALIDATED")
        return {"status": "PASS" if success else "FAIL", "exit_code": cp.returncode,
                "result": payload, "stderr_tail": cp.stderr[-1200:],
                "scope": "ORIGINAL_STAGE4_INDEPENDENT_PHYSICAL_INVENTORY_SETTLEMENT_CARRYOVER_REPLAY"}
    except subprocess.TimeoutExpired:
        return {"status": "FAIL", "reason": "INDEPENDENT_REPLAY_TIMEOUT"}
    except OSError as exc:
        return {"status": "FAIL", "reason": "REPLAY_EXECUTION_FAILED", "detail": str(exc)}


def _audit_saved(config_path: Path, config: dict, snapshot: dict,
                 output_root: Path, run_id: str, timeout: int) -> dict:
    audit = {"status": "INCONCLUSIVE", "run_id": run_id,
             "scope": "S5_5_CALLS_2_WINDOWS_ONLY", "source_verified": True,
             "simulation_only": True, "operational_approval": False,
             "global_58_day_optimality_proven": False,
             "database_access_performed": False, "issues": []}
    try:
        directory = resolve_run(output_root, run_id)
        saved = load_json(directory / "execution_report.json")
        audit["execution_status"] = saved.get("status")
        audit["stopping_reason"] = saved.get("stopping_reason")
        audit["execution_report_path"] = str(directory / "execution_report.json")
        structural = audit_rolling_run(directory, require_integrated=True)
        audit["structural"] = {"status": structural["status"],
                               "inspected_checkpoint_count": structural["inspected_checkpoint_count"],
                               "issues": structural["issues"]}
        if structural["status"] != "STRUCTURAL_CHECKPOINT_CHAIN_VALID":
            audit["issues"].append({"code": "STRUCTURAL_AUDIT_FAILED"})
            return audit
        manifest, windows = load_checkpoints(directory)
        expected_source_hash = digest(canonical(snapshot).encode("utf-8"))
        if (manifest["config"].get("source_fingerprint") != expected_source_hash
                or saved.get("source_fingerprint") != expected_source_hash):
            audit["issues"].append({"code": "ORIGINAL_SOURCE_FINGERPRINT_MISMATCH"})
            return audit
        evidence = inspect_s5_evidence(snapshot, saved, manifest, windows)
        audit["evidence"] = evidence
        if evidence["status"] != "PASS_EVIDENCE_ONLY":
            audit["issues"].append({"code": "SELECTED_SCHEDULE_EVIDENCE_FAILED"})
            return audit
        if not isinstance(saved.get("unresolved_accounting_call_ids"), list):
            audit["issues"].append({"code": "INVALID_ACCOUNTING_COVERAGE_METADATA"})
            return audit
        audit["replay"] = _run_replay(config_path, output_root, run_id, timeout)
        if audit["replay"]["status"] != "PASS":
            audit["issues"].append({"code": "INDEPENDENT_STAGE4_REPLAY_FAILED"})
            return audit
        audit["status"] = "PASS_S5_INTEGRATED_VALIDATION_ONLY"
        audit["limitations"] = [
            "Five-call restricted source scenario; other 135 calls are excluded.",
            "No actual port operational approval, even when synthetic policy runs pass.",
            "Optimality is limited to the generated finite candidate domain per window.",
            "Overlapping 72-hour projected window costs are NOT additive.",
            "S5 window validation is not proof that all scoped calls finished.",
            "No proof of global 140-call or 58-day optimality.",
        ]
        return audit
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        audit["issues"].append({"code": "SAVED_RUN_AUDIT_ERROR",
                                "type": type(exc).__name__, "reason": str(exc)})
        return audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S5-only V5 optimization and independent replay audit (no DB)")
    parser.add_argument("--mode", choices=("preflight", "run", "audit-existing"), required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=ROOT / "results" / "v5_optimizer_audit_runs")
    parser.add_argument("--run-id", help="Required only with --mode audit-existing")
    parser.add_argument("--seconds-per-window", type=float, default=60)
    parser.add_argument("--replay-timeout-seconds", type=int, default=300)
    parser.add_argument("--report-json", type=Path, help="Optional new audit summary path; refuses overwrite")
    args = parser.parse_args(argv)
    if args.mode == "audit-existing" and not args.run_id:
        parser.error("--run-id required for audit-existing")
    if args.mode != "audit-existing" and args.run_id:
        parser.error("--run-id only valid for audit-existing")
    if not 1 <= args.seconds_per_window <= 1800 or not 1 <= args.replay_timeout_seconds <= 3600:
        parser.error("Time budgets out of range")
    if args.report_json is not None and args.report_json.expanduser().exists():
        parser.error("--report-json already exists; choose a new path (never overwrite audit history)")
    report: dict
    try:
        cfg_path = args.config.expanduser().resolve()
        config = load_execution_config(cfg_path)
        root = args.output_root.expanduser().resolve()
        source = config["data_dir"].resolve()
        if root == source or source in root.parents:
            raise ValueError("Output directory may not be inside V5 source directory")
        if args.report_json is not None:
            dest = args.report_json.expanduser().resolve()
            if dest == source or source in dest.parents:
                raise ValueError("Audit report may not be written inside source directory")
        snapshot, preflight = preflight_sources(config)
        source_check = source_evidence(snapshot, preflight)
        if source_check["status"] != "PASS_INPUT_STRUCTURE_ONLY":
            report = {"status": "BLOCKED_INPUT", "source": source_check}
            _output(report, args.report_json)
            return 2
        if args.mode == "preflight":
            _output({"status": "PASS_SOURCE_PREFLIGHT_ONLY", "source": source_check,
                     "note": "No CP-SAT or independent replay executed"}, args.report_json)
            return 0
        if args.mode == "run":
            result = execute_scale(config, output_root=root, call_count=5, windows=2,
                                   time_limit_seconds=args.seconds_per_window,
                                   candidates_per_pair=6, total_candidates=240,
                                   improvement_rounds=2, snapshot=snapshot, preflight=preflight)
            run_id = result["run_id"]
            if result.get("status") != "REQUESTED_WINDOWS_VALIDATED":
                report = {"status": "BLOCKED_EXECUTION", "source": source_check,
                          "run_id": run_id, "execution": result,
                          "note": "No success inferred from failed or incomplete windows"}
                _output(report, args.report_json)
                return 2
        else:
            run_id = args.run_id
        report = _audit_saved(cfg_path, config, snapshot, root,
                              run_id, args.replay_timeout_seconds)
        report["source"] = source_check
        _output(report, args.report_json)
        return 0 if report["status"] == "PASS_S5_INTEGRATED_VALIDATION_ONLY" else 2
    except (OSError, ValueError, KeyError, TypeError, ImportError, ScaleInputError) as exc:
        report = {"status": "BLOCKED_INPUT_OR_RUNTIME", "error_type": type(exc).__name__,
                  "reason": str(exc), "database_access_performed": False,
                  "simulation_only": True, "operational_approval": False}
        _output(report, args.report_json)
        return 2


if __name__ == "__main__":
    sys.exit(main())
