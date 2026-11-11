"""Read-only, unified Phase 4 reporting over existing independent validators.

A saved "VALID" flag is never proof: replay each incumbent from source inputs.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

VERSION = "V3_PHASE4_UNIFIED_VALIDATION_V1"


def _check(status, reason, **extra):
    return dict(status=status, reason=reason, **extra)


def _money(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        amount = Decimal(str(value))
        return amount if amount.is_finite() and amount >= 0 and amount == amount.to_integral_value() else None
    except (TypeError, ValueError, InvalidOperation):
        return None


def _source_preflight(snapshot):
    try:
        from ..integrated_input.validator import validate
        if snapshot["fixed_process_flows"].get("extension_provenance"):
            from ..integrated_input.flow_extension import validate_extended_snapshot
            report = validate_extended_snapshot(snapshot)
        else:
            report = validate(snapshot)
        return _check("PASS" if report.get("data_preflight_pass") else "FAIL",
                      "Existing V3 source and flow preflight",
                      issues=(report.get("issues") or [])[:20],
                      pending_contract_count=len(report.get("pending_contract_validation") or []))
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return _check("FAIL", "Source validation failed", issues=[dict(code="PREFLIGHT_ERROR", reason=str(exc))])


def validate_phase4_result(snapshot, result, *, policy, as_of_at, commitments=(),
                           completed_rows=(), initial_inventory=None, carried_transfers=None,
                           tank_commitments=None, same_tank_policy=None, settlement_records=(),
                           pricing_policy=None):
    """Fresh physical, FIX, tank, inventory and gross-cost replay of a finite-domain result."""
    from ..integrated_input.flow_extension import check_extension_available
    from .independent_validator import validate_simulation_candidate
    pricing = pricing_policy or result.get("pricing_policy") or "EXPLICIT_EVENTS_V1"
    iterations = result.get("iterations") or []
    costs = [_money(x.get("demurrage_cents")) for x in iterations if isinstance(x, dict)]
    costs = [v for v in costs if v is not None]
    incumbent = result.get("incumbent")
    best = _money(incumbent.get("total_demurrage_cents")) if isinstance(incumbent, dict) else None
    metrics = dict(solver_status=result.get("status"),
                   elapsed_seconds_reported=result.get("elapsed_seconds"),
                   candidates_evaluated=len(iterations), candidates_costed=len(costs),
                   first_cost_cents=str(costs[0]) if costs else None,
                   best_cost_cents=str(best) if best is not None else None,
                   improvement_cents=str(costs[0] - best) if costs and best is not None else None,
                   validated_incumbent_present=False,
                   domain_exhausted_reported=result.get("domain_exhausted") is True)
    checks = dict(source_preflight=_source_preflight(snapshot),
                  operational_commitment_authority=_check("NOT_RUN", "Operating DB authority not independently checked"),
                  observed_weather_and_sof=_check("NOT_RUN", "Synthetic events are not observed weather or SOF"),
                  continuous_time_optimality=_check("NOT_RUN", "Finite ETB domain only"))
    report = dict(report_version=VERSION, run_type="SINGLE_72H", status="INCOMPLETE",
                  as_of_at=as_of_at, pricing_policy=pricing,
                  same_tank_policy=same_tank_policy, metrics=metrics, checks=checks,
                  issues=[], operational_approval=False)
    if checks["source_preflight"]["status"] != "PASS":
        report["status"] = "FAIL"
        report["issues"].extend(checks["source_preflight"].get("issues") or [])
        checks["independent_candidate_replay"] = _check("NOT_RUN", "Failed source preflight")
        return report
    if not isinstance(incumbent, dict):
        checks["independent_candidate_replay"] = _check("NOT_RUN", "No incumbent to replay")
        report["issues"].append(dict(code="NO_INCUMBENT", reason=str(result.get("status"))))
        return report
    try:
        if snapshot["fixed_process_flows"].get("extension_provenance"):
            check_extension_available(snapshot, as_of_at=as_of_at,
                                      projection_end_at=incumbent["tank_validation"]["projection_end_at"])
        checks["flow_knowledge"] = _check("PASS", "Future process flow known-at rule verified")
    except (ValueError, KeyError, TypeError) as exc:
        checks["flow_knowledge"] = _check("FAIL", "Future-information leakage or missing projection")
        checks["independent_candidate_replay"] = _check("NOT_RUN", "Flow provenance failed")
        report["issues"].append(dict(code="FLOW_KNOWLEDGE", reason=str(exc)))
        report["status"] = "FAIL"
        return report
    try:
        replay = validate_simulation_candidate(
            snapshot, incumbent, policy=policy, as_of_at=as_of_at,
            commitments=commitments, completed_rows=completed_rows,
            initial_inventory=initial_inventory, carried_transfers=carried_transfers,
            tank_commitments=tank_commitments, same_tank_policy=same_tank_policy,
            settlement_records=settlement_records, pricing_policy=pricing)
        passed = replay.get("status") == "VALID"
        checks["independent_candidate_replay"] = _check(
            "PASS" if passed else "FAIL",
            "Freshly replayed berth, tank, inventory, FIX and cost",
            issues=(replay.get("issues") or [])[:30])
        for subsystem in ("physical_and_fix", "tank_and_inventory", "contract_settlement", "horizon_coverage"):
            checks[subsystem] = _check("PASS" if passed else "NOT_RUN",
                                        "All checked by independent replay" if passed else "See replay issues")
        metrics["validated_incumbent_present"] = passed
        report["status"] = "PASS" if passed else "FAIL"
        if not passed:
            report["issues"].extend(replay.get("issues") or [dict(code="REPLAY_NOT_VALID", reason=str(replay.get("status")))])
    except (ValueError, KeyError, TypeError, ZeroDivisionError, OverflowError) as exc:
        checks["independent_candidate_replay"] = _check("FAIL", "Replay raised an exception")
        report["issues"].append(dict(code="REPLAY_EXCEPTION", reason=str(exc)))
        report["status"] = "FAIL"
    return report


def validate_phase4_rolling(snapshot, result, *, policy, expected_days,
                            same_tank_policy=None, settlement_records=()):
    """Recompute every 24h carried state and fail closed on missing windows."""
    from ..integrated_input.validator import timestamp
    from .synthetic_rolling import simulated_boundary_state
    if type(expected_days) is not int or expected_days < 1:
        raise ValueError("expected_days must be positive")
    steps = result.get("steps") or []
    report = dict(report_version=VERSION, run_type="ROLLING_72H_EVERY_24H",
                  status="INCOMPLETE", expected_days=expected_days,
                  attempted_days=len(steps), validated_days=0, steps=[], issues=[],
                  operational_approval=False)
    if not steps:
        report["issues"].append(dict(code="NO_STEPS"))
        return report
    state = None
    previous = None
    for day, entry in enumerate(steps, 1):
        at = entry.get("as_of_at")
        if not at or (previous and timestamp(at) != timestamp(previous) + timedelta(hours=24)):
            report["status"] = "FAIL"
            report["issues"].append(dict(code="NON_CONTIGUOUS_OR_MISSING_TIME", day=day))
            break
        kwargs = {} if state is None else {key: state[key] for key in (
            "completed_rows", "commitments", "tank_commitments", "initial_inventory", "carried_transfers")}
        if state:
            for field, key in (("completed_call_ids", "completed_rows"), ("protected_call_ids", "commitments")):
                if entry.get(field) is None or sorted(entry[field]) != sorted(x["call_id"] for x in state[key]):
                    report["status"] = "FAIL"
                    report["issues"].append(dict(code="ROLLING_STATE_DECLARATION_MISMATCH", day=day))
                    return report
        checked = validate_phase4_result(snapshot, entry.get("result") or {}, policy=policy,
                                         as_of_at=at, same_tank_policy=same_tank_policy,
                                         settlement_records=settlement_records, **kwargs)
        report["steps"].append(dict(day=day, as_of_at=at, validation=checked))
        if checked["status"] != "PASS":
            report["status"] = checked["status"]
            report["issues"].append(dict(code="WINDOW_NOT_VALIDATED", day=day))
            break
        candidate = entry["result"]["incumbent"]
        tank = candidate["tank_validation"]
        if state and any(tank["inventories"][tid][0]["inventory_bbl"] != amount
                         for tid, amount in state["initial_inventory"].items()):
            report["status"] = "FAIL"
            report["issues"].append(dict(code="CARRYOVER_MISMATCH", day=day))
            break
        synthetic = dict(status="PHYSICAL_AND_TANK_ONLY",
                         berth_result=dict(schedule=candidate["berth_schedule"]),
                         validation=dict(status="PHYSICAL_AND_TANK_ONLY",
                                         source_fingerprint=candidate["source_fingerprint"],
                                         policy_fingerprint=candidate["policy_fingerprint"],
                                         tank_result=tank))
        try:
            state = simulated_boundary_state(snapshot, synthetic, policy=policy,
                                             completed_rows=kwargs.get("completed_rows", ()))
            if timestamp(state["boundary"]) != timestamp(at) + timedelta(hours=24):
                raise ValueError("Wrong carry boundary")
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            report["status"] = "FAIL"
            report["issues"].append(dict(code="CARRYOVER_REPLAY_FAILED", day=day, reason=str(exc)))
            break
        report["validated_days"] += 1
        previous = at
    else:
        stored = result.get("last_simulated_state")
        if stored is None:
            report["issues"].append(dict(code="MISSING_LAST_STATE"))
        elif any(stored.get(k) != state.get(k) for k in (
                "boundary", "initial_inventory", "carried_transfers",
                "tank_commitments", "commitments", "completed_rows")):
            report["status"] = "FAIL"
            report["issues"].append(dict(code="LAST_STATE_MISMATCH"))
        elif len(steps) == expected_days and result.get("status") == "REQUESTED_WINDOWS_VALIDATED":
            report["status"] = "PASS"
        else:
            report["issues"].append(dict(code="ROLLING_COVERAGE_UNPROVEN"))
    return report


def render_markdown(report):
    lines = ["# Docktimize Phase 4 validation", "",
             "- Result: **" + report["status"] + "**",
             "- Report version: " + report["report_version"],
             "- Operational approval: **false**", ""]
    if report["run_type"] == "SINGLE_72H":
        metrics = report["metrics"]
        for key in ("solver_status", "elapsed_seconds_reported", "candidates_evaluated",
                    "candidates_costed", "first_cost_cents", "best_cost_cents", "improvement_cents"):
            lines.append("- " + key + ": " + str(metrics[key]))
        lines.append("")
        for key, value in report["checks"].items():
            lines.append("- " + key + ": **" + value["status"] + "** (" + value["reason"] + ")")
    else:
        lines.append("- Windows validated/attempted: " +
                     str(report["validated_days"]) + "/" + str(report["attempted_days"]))
        for step in report["steps"]:
            lines.append("- Day " + str(step["day"]) + ": " + step["validation"]["status"])
    for issue in report.get("issues", [])[:20]:
        lines.append("- Blocking: " + str(issue))
    lines += ["", "> PASS denotes synthetic replay only. Not operational approval or global optimality.", ""]
    return "\n".join(lines)
