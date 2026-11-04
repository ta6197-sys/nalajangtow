"""Read-only, fail-closed SK physical compatibility preflight (Stage 2).

No changes to existing optimizer, PostgreSQL, or committed schedules.
PASS covers only the selected documentary checks; operational clearance
is never inferred from physical compatibility.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Iterable

from .berth_rules import (load_facilities, load_mapping, validate_mapping,
                           check_call_against_virtual_berth, NOT_EVALUATED)

EXPECTED_BERTHS = tuple(f"BERTH-{i:02d}" for i in range(1, 9))
REQUIRED_VESSEL_FIELDS = ("draft_m", "dwt", "loa_m", "cargo_type")
KNOWN_FIELDS = {"call_id", "vessel_id", "draft_m", "dwt", "loa_m", "gt", "cargo_type", "data_origin"}


class SKPreflightError(ValueError):
    pass


def load_call_profiles(path: str | Path) -> dict[str, dict[str, Any]]:
    """Strict format check, but permit null dimensions as explicitly unknown."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise SKPreflightError("Profiles must be an object with schema_version=1")
    rows = payload.get("calls")
    if not isinstance(rows, list):
        raise SKPreflightError("'calls' must be a list")
    from decimal import Decimal, InvalidOperation
    profiles = {}
    for n, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise SKPreflightError(f"calls[{n}] must be an object")
        extra = set(row) - KNOWN_FIELDS
        if extra:
            raise SKPreflightError(f"calls[{n}] unexpected fields: {', '.join(sorted(extra))}")
        cid = row.get("call_id")
        if not isinstance(cid, str) or not cid.strip() or cid != cid.strip():
            raise SKPreflightError(f"calls[{n}].call_id required (trim whitespace)")
        if cid in profiles:
            raise SKPreflightError(f"Duplicate call_id: {cid}")
        for field in ("draft_m", "dwt", "loa_m", "gt"):
            v = row.get(field)
            if v is None or v == "":
                continue
            if isinstance(v, bool):
                raise SKPreflightError(f"{cid}.{field}: boolean not allowed")
            try:
                parsed = Decimal(str(v))
            except (InvalidOperation, TypeError) as exc:
                raise SKPreflightError(f"{cid}.{field}: invalid number") from exc
            if not parsed.is_finite() or parsed <= 0:
                raise SKPreflightError(f"{cid}.{field}: must be finite and positive")
        for field in ("cargo_type", "vessel_id", "data_origin"):
            v = row.get(field)
            if v is not None and not isinstance(v, str):
                raise SKPreflightError(f"{cid}.{field}: expected text or null")
        profiles[cid] = row
    return profiles


def audit_mapping(mapping: Mapping[str, Any], facilities: Mapping[str, dict]) -> dict:
    errors = validate_mapping(mapping, facilities)
    slots = [
        {"berth_id": bid,
         "facility_code": mapping.get(bid),
         "facility_type": facilities.get(mapping.get(bid), {}).get("facility_type")}
        for bid in EXPECTED_BERTHS
    ]
    return {"status": "READY" if not errors else "NOT_READY",
            "selected_count": sum(bool(mapping.get(bid)) for bid in EXPECTED_BERTHS),
            "expected_count": 8, "issues": errors, "slots": slots}


def audit_calls(mapping: Mapping[str, Any], facilities: Mapping[str, dict],
                profiles: Mapping[str, dict], call_ids: Iterable[str]) -> dict:
    """Assess source-backed physical compatibility only, without scheduling.

    Only PASS combinations may be used later in a strict physical filter.
    A PASS does NOT indicate weather, mooring or terminal approval.
    """
    mapping_audit = audit_mapping(mapping, facilities)
    ids = list(call_ids)
    if len(ids) != len(set(ids)):
        raise SKPreflightError("Duplicate call IDs supplied for audit")
    items = []
    for cid in ids:
        if not isinstance(cid, str) or not cid.strip():
            raise SKPreflightError("All call IDs must be nonempty strings")
        profile = profiles.get(cid)
        if mapping_audit["status"] != "READY":
            items.append({"call_id": cid, "status": "BLOCKED", "eligible_berths": [],
                          "reason": "BERTH_MAPPING_NOT_READY", "berths": []})
        elif profile is None:
            items.append({"call_id": cid, "status": "BLOCKED", "eligible_berths": [],
                          "reason": "VESSEL_PROFILE_MISSING", "berths": []})
        else:
            results = [check_call_against_virtual_berth(profile, bid, mapping, facilities)
                       for bid in EXPECTED_BERTHS]
            passed = [r["berth_id"] for r in results if r["status"] == "PASS"]
            unknown = [r["berth_id"] for r in results if r["status"] == "UNKNOWN"]
            items.append({"call_id": cid, "status": "READY" if passed else "BLOCKED",
                          "eligible_berths": passed,
                          "reason": "HAS_PHYSICAL_OPTIONS" if passed else
                                    "ALL_BERTHS_FAIL_OR_UNKNOWN",
                          "unknown_berths": unknown,
                          "berths": [
                              {"berth_id": r["berth_id"],
                               "facility_code": r.get("facility_code"),
                               "status": r["status"],
                               "issues": r["issues"]}
                              for r in results]})
    counts = Counter(c["status"] for c in items)
    return {"audit_version": 1,
            "scope": "PHYSICAL_ONLY_NOT_OPERATIONAL_CLEARANCE",
            "mapping": mapping_audit,
            "summary": {"calls": len(items), "ready": counts["READY"],
                        "blocked": counts["BLOCKED"],
                        "ready_for_strict_physical_filter": mapping_audit["status"] == "READY" and not counts["BLOCKED"] and bool(items)},
            "not_evaluated": list(NOT_EVALUATED), "calls": items}


def create_blank_profiles(call_ids: Iterable[str]) -> dict:
    ids = list(call_ids)
    if len(ids) != len(set(ids)) or any(not isinstance(c, str) or not c.strip() for c in ids):
        raise SKPreflightError("call_id list must have distinct nonempty strings")
    return {"schema_version": 1, "purpose": "MANUAL_REVIEW_NOT_AUTOMATIC_SYNTHESIS",
            "calls": [{"call_id": cid, "vessel_id": None, "draft_m": None,
                       "dwt": None, "loa_m": None, "gt": None,
                       "cargo_type": None, "data_origin": "UNVERIFIED"}
                      for cid in ids]}
