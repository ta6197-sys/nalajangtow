"""Read-only structural audit of V5 integrated rolling checkpoints.

This is NOT a substitute for run_v5_rolling(resume=True), which performs
independent physical/inventory/contract replays using the original inputs.
No source files, checkpoints or database tables are modified here.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
import hashlib
import json
import re

_WINDOW = re.compile(r"window_(\d{3})\.json\Z")


def _fingerprint(payload):
    """Exactly match integrated_input.provenance.canonical/digest for JSON."""
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timestamp must have an explicit timezone offset")
    return result


def _load(path):
    with path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return payload


def _check_quantity_progress(previous, current, errors, window_id):
    for cid, progress in current.items():
        try:
            completed = Fraction(progress["completed_bbl"])
            remaining = Fraction(progress["remaining_bbl"])
            if completed < 0 or remaining < 0:
                errors.append(f"window {window_id}: negative cargo quantity for {cid}")
            if cid in previous:
                past = previous[cid]
                past_completed = Fraction(past["completed_bbl"])
                past_remaining = Fraction(past["remaining_bbl"])
                if completed < past_completed:
                    errors.append(f"window {window_id}: cargo progress regressed for {cid}")
                if completed + remaining != past_completed + past_remaining:
                    errors.append(f"window {window_id}: total cargo quantity changed for {cid}")
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            errors.append(f"window {window_id}: malformed cargo progress {cid}: {exc}")


def audit_rolling_run(folder, *, require_integrated=True):
    """Audit persisted checkpoint structure; return a report without writing.

    This intentionally does NOT assert independently validated physical,
    tank, or contract correctness. The only authoritative replay for those is
    run_v5_rolling(..., resume=True) with the original, verified input bundle.
    """
    base = Path(folder).resolve()
    errors = []
    summaries = []
    report = {
        "status": "INVALID_CHECKPOINT_CHAIN",
        "checkpoint_dir": str(base),
        "fingerprint_algorithm": "SHA256_CANONICAL_JSON",
        "verification_scope": "STRUCTURAL_AND_FINGERPRINT_ONLY_NOT_PHYSICAL_REPLAY",
        "independent_replay_performed": False,
        "validated_checkpoint_count": 0,
        "inspected_checkpoint_count": 0,
        "rolling_optimizer_mode": None,
        "run_fingerprint": None,
        "windows": summaries,
        "issues": errors,
    }

    try:
        if not base.is_dir():
            raise ValueError("Checkpoint directory does not exist")
        manifest = _load(base / "manifest.json")
        config = manifest["config"]
        if not isinstance(config, dict):
            raise ValueError("Manifest config must be an object")
        run_id = manifest["run_fingerprint"]
        report["run_fingerprint"] = run_id
        if run_id != _fingerprint(config):
            errors.append("manifest: run fingerprint mismatch")
        mode = config.get("rolling_optimizer_mode", "LEGACY_ENUMERATION")
        report["rolling_optimizer_mode"] = mode
        if require_integrated and mode != "INTEGRATED_COST_AWARE_V1":
            errors.append("manifest: expected INTEGRATED_COST_AWARE_V1")
        if mode == "INTEGRATED_COST_AWARE_V1":
            for field, expected in (
                ("objective_mode", "MIN_TOTAL_DEMURRAGE"),
                ("inventory_constraint_mode", "EXACT_EVENT_BALANCE_V1"),
                ("candidate_generation_mode", "AUTO_ADAPTIVE_CANDIDATES"),
                ("candidate_improvement_mode", "COST_AWARE_V1"),
            ):
                if config.get(field) != expected:
                    errors.append(f"manifest: wrong {field}")
        if manifest.get("operational_approval") is not False:
            errors.append("manifest: must explicitly disclaim operational approval")

        paths = sorted(p for p in base.iterdir() if _WINDOW.fullmatch(p.name))
        ids = [int(_WINDOW.fullmatch(p.name).group(1)) for p in paths]
        if ids != list(range(1, len(ids) + 1)):
            errors.append("checkpoints: noncontiguous window IDs")
        if not paths:
            errors.append("checkpoints: no saved validated window")
        expected_start = _time(config["start_at"])
        previous_state = None
        previous_completed = set()
        previous_progress = {}
        for i, path in enumerate(paths, start=1):
            cp = _load(path)
            prefix = f"window {i}"
            if cp.get("window_id") != i:
                errors.append(f"{prefix}: window_id mismatch")
            if cp.get("run_fingerprint") != run_id:
                errors.append(f"{prefix}: run fingerprint mismatch")
            if cp.get("optimization_configuration") != config:
                errors.append(f"{prefix}: optimization configuration mismatch")
            if cp.get("input_fingerprint") != config.get("source_fingerprint"):
                errors.append(f"{prefix}: source fingerprint mismatch")
            if cp.get("policy_fingerprint") != config.get("policy_fingerprint"):
                errors.append(f"{prefix}: policy fingerprint mismatch")
            body = dict(cp)
            stored_fp = body.pop("checkpoint_fingerprint", None)
            if stored_fp != _fingerprint(body):
                errors.append(f"{prefix}: checkpoint fingerprint mismatch")
            state = cp.get("next_state")
            if not isinstance(state, dict):
                errors.append(f"{prefix}: next_state is missing")
                continue
            if cp.get("next_state_fingerprint") != _fingerprint(state):
                errors.append(f"{prefix}: next state fingerprint mismatch")
            if previous_state is not None and cp.get("prior_state_fingerprint") != _fingerprint(previous_state):
                errors.append(f"{prefix}: prior state fingerprint chain mismatch")
            expected_at = expected_start + timedelta(hours=24 * (i - 1))
            try:
                actual_at = _time(cp["as_of_at"])
                horizon_end = _time(cp["planning_horizon_end"])
                if actual_at != expected_at:
                    errors.append(f"{prefix}: rolling window is not exactly 24 hours after preceding")
                if horizon_end != actual_at + timedelta(hours=72):
                    errors.append(f"{prefix}: planning horizon is not 72 hours")
                if _time(state["at"]) != actual_at + timedelta(hours=24):
                    errors.append(f"{prefix}: carried state boundary is not 24 hours")
            except (KeyError, ValueError, TypeError) as exc:
                errors.append(f"{prefix}: invalid timestamps: {exc}")
            if cp.get("freeze_boundary") is not None or cp.get("dynamic_lock_horizon_hours") != 5:
                errors.append(f"{prefix}: dynamic-lock metadata mismatch")
            validations = cp.get("validation_results", {})
            rows = cp.get("selected_schedule", [])
            if not isinstance(rows, list):
                errors.append(f"{prefix}: selected_schedule is not a list")
                rows = []
            ids_in_schedule = [r.get("call_id") for r in rows if isinstance(r, dict)]
            if len(ids_in_schedule) != len(rows) or len(set(ids_in_schedule)) != len(ids_in_schedule):
                errors.append(f"{prefix}: duplicate or malformed selected call IDs")
            expected_validation = "VALID" if rows else "NO_WORK"
            if validations.get("physical") != expected_validation:
                errors.append(f"{prefix}: validation flag mismatch (physical)")
            if validations.get("settlement") != ("SETTLED" if rows else "NO_WORK"):
                errors.append(f"{prefix}: validation flag mismatch (settlement)")
            if validations.get("inventory") != "VALID" or validations.get("carryover") != "VALID":
                errors.append(f"{prefix}: missing inventory/carryover validation flag")
            protected = cp.get("protected_commitments_in", [])
            if not isinstance(protected, list) or any(p not in rows for p in protected):
                errors.append(f"{prefix}: protected physical commitment absent or modified")
            completed = cp.get("completed_calls", [])
            if not isinstance(completed, list) or not set(previous_completed).issubset(set(completed)):
                errors.append(f"{prefix}: previously completed call disappeared")
            previous_completed = set(completed) if isinstance(completed, list) else previous_completed
            if completed != [r.get("call_id") for r in state.get("completed_rows", [])]:
                errors.append(f"{prefix}: completed list differs from next_state")
            for field, key in (("carried_inventory", "inventory"), ("carried_transfers", "carried_transfers"),
                               ("cargo_progress", "cargo_progress"), ("contract_accounting_state", "contract_accounting_state"),
                               ("in_progress_contract_accounting", "in_progress_contract_accounting")):
                if cp.get(field) != state.get(key):
                    errors.append(f"{prefix}: checkpoint and next_state differ for {field}")
            current_progress = cp.get("cargo_progress", {})
            if not isinstance(current_progress, dict):
                errors.append(f"{prefix}: malformed cargo_progress")
                current_progress = {}
            _check_quantity_progress(previous_progress, current_progress, errors, i)
            previous_progress = current_progress
            summary = cp.get("summary", {})
            if summary.get("window_id") != i or summary.get("as_of_at") != cp.get("as_of_at"):
                errors.append(f"{prefix}: summary identity mismatch")
            candidate = cp.get("candidate")
            if rows:
                if not isinstance(candidate, dict):
                    errors.append(f"{prefix}: missing selected candidate")
                else:
                    if candidate.get("selected_schedule") != rows:
                        errors.append(f"{prefix}: candidate schedule differs from checkpoint")
                    if summary.get("window_demurrage_cents") != candidate.get("total_demurrage_cents"):
                        errors.append(f"{prefix}: candidate cost differs from summary")
                    if mode == "INTEGRATED_COST_AWARE_V1":
                        opt = candidate.get("optimizer_result", {})
                        domain = opt.get("final_candidate_domain", {})
                        incumbent = opt.get("best_validated_incumbent", {})
                        proven = domain.get("optimality_proven")
                        if proven is None or proven != opt.get("final_domain_optimality_proven") or proven != summary.get("final_domain_optimality_proven"):
                            errors.append(f"{prefix}: inconsistent final-domain optimality proof")
                        if proven is not True and (summary.get("solver_status") == "OPTIMAL" or opt.get("optimization_status") == "OPTIMAL"):
                            errors.append(f"{prefix}: stale OPTIMAL reported for unproven expanded domain")
                        if incumbent.get("total_demurrage_cents") != summary.get("window_demurrage_cents"):
                            errors.append(f"{prefix}: best incumbent and summary cost mismatch")
                        if domain.get("candidate_domain_fingerprint") != opt.get("candidate_domain_fingerprint"):
                            errors.append(f"{prefix}: final candidate-domain fingerprint mismatch")
                        if summary.get("best_demurrage_cents") != summary.get("window_demurrage_cents"):
                            errors.append(f"{prefix}: best cost differs from window cost")
            elif candidate is not None:
                errors.append(f"{prefix}: unexpected candidate in empty window")
            summaries.append({
                "window_id": i,
                "as_of_at": cp.get("as_of_at"),
                "selected_call_count": len(rows),
                "window_projected_demurrage_cents": summary.get("window_demurrage_cents"),
                "final_domain_optimality_proven": summary.get("final_domain_optimality_proven") if rows else None,
                "carried_inventory": cp.get("carried_inventory"),
                "completed_call_ids": sorted(previous_completed),
            })
            previous_state = state
        report["inspected_checkpoint_count"] = len(paths)
    except (OSError, KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError) as exc:
        errors.append(f"audit input error: {type(exc).__name__}: {exc}")
    if not errors:
        report["status"] = "STRUCTURAL_CHECKPOINT_CHAIN_VALID"
        report["validated_checkpoint_count"] = report["inspected_checkpoint_count"]
    return report
