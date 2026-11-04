"""Opt-in source-backed SINGLE physical-berth checks for Docktimize V3.

The vessel characteristics are explicitly SYNTHETIC.  This is documentary
compatibility, not real-world berthing approval.  UNKNOWN never passes.
No pair/combined berth is modeled. No DB schema/table is changed.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .berth_rules import load_facilities, load_mapping
from .sk_preflight import audit_calls, audit_mapping
from .input_loader import fingerprint

ROOT = Path(__file__).resolve().parents[2]
POLICY = "SK_SINGLE_BERTH_DOCUMENTARY_V3"
EXPECTED_MAPPING = {
    "BERTH-01": "SK#1-1", "BERTH-02": "SK#2-7", "BERTH-03": "SK#3",
    "BERTH-04": "SK#4-1", "BERTH-05": "SK#5-5", "BERTH-06": "SK#6",
    "BERTH-07": "SK#7", "BERTH-08": "SK#8",
}
KNOWN_PROFILE_FIELDS = {
    "call_id", "vessel_id", "draft_m", "dwt", "loa_m", "gt", "cargo_type",
    "data_origin", "size_class", "verification_status", "target_berth_id",
}
COMPATIBILITY_FIELDS = {
    "call_id", "vessel_id", "draft_m", "dwt", "loa_m", "gt", "cargo_type", "data_origin"
}


def _source_files():
    return {
        "mapping": Path(os.environ.get(
            "SK_PHYSICAL_MAPPING_FILE", ROOT / "config/berth_mapping.json")).resolve(),
        "facilities": Path(os.environ.get(
            "SK_PHYSICAL_FACILITIES_FILE", ROOT / "data/sk_facilities.json")).resolve(),
        "profiles": Path(os.environ.get(
            "SK_PHYSICAL_PROFILES_FILE", ROOT / "input/vessel_profiles.sk-v3-independent.json")).resolve(),
    }


def read_profiles(path):
    """Accept calls[] (canonical) or profiles[] (earlier independent generator).

    A tolerated target_berth_id must be NULL: no hidden pre-assignment.
    Only whitelisted values enter the frozen compatibility snapshot.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or payload.get("data_origin") != "SYNTHETIC":
        raise ValueError("Profiles must declare schema_version=1 and data_origin=SYNTHETIC")
    rows = payload.get("calls")
    if rows is None:
        rows = payload.get("profiles")
    if not isinstance(rows, list):
        raise ValueError("Vessel profile file must contain calls[] or profiles[]")
    output = {}
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Profile {index} must be an object")
        unknown = set(raw) - KNOWN_PROFILE_FIELDS
        if unknown:
            raise ValueError(f"Profile {index}: unexpected fields {sorted(unknown)}")
        cid = raw.get("call_id")
        if not isinstance(cid, str) or not cid.strip() or cid != cid.strip() or cid in output:
            raise ValueError(f"Invalid or duplicate call_id at profile {index}: {cid}")
        if raw.get("target_berth_id") is not None:
            raise ValueError(f"{cid}: preselected target_berth_id forbidden")
        if raw.get("data_origin") != "SYNTHETIC":
            raise ValueError(f"{cid}: missing explicit SYNTHETIC provenance")
        if not isinstance(raw.get("cargo_type"), str) or not raw["cargo_type"].strip():
            raise ValueError(f"{cid}: cargo_type required")
        if not isinstance(raw.get("vessel_id"), str) or not raw["vessel_id"].strip():
            raise ValueError(f"{cid}: vessel_id required")
        for field in ("dwt", "loa_m", "draft_m", "gt"):
            value = raw.get(field)
            if value is None or isinstance(value, bool):
                raise ValueError(f"{cid}.{field}: positive SYNTHETIC numeric input required")
            try:
                number = Decimal(str(value))
            except (InvalidOperation, TypeError) as exc:
                raise ValueError(f"{cid}.{field}: not a number") from exc
            if not number.is_finite() or number <= 0:
                raise ValueError(f"{cid}.{field}: positive finite number required")
        output[cid] = {key: raw[key] for key in COMPATIBILITY_FIELDS if key in raw}
    return output


def _load_source(mapping_path, facilities_path):
    mapping = load_mapping(mapping_path)
    facilities = load_facilities(facilities_path)
    check = audit_mapping(mapping, facilities)
    if check["status"] != "READY" or mapping != EXPECTED_MAPPING:
        raise ValueError(
            "Expected 8 SK piers' selected physical facilities and valid 1:1 mapping: "
            + str(check["issues"])
        )
    return mapping, facilities


def build_sk_snapshot_for_experiment(experiment_key, calls, berths):
    """Opt-in strict preflight. Fail BEFORE CP-SAT if any active call lacks a PASS.

    Set SK_PHYSICAL_EXPERIMENT_KEY to a distinct NEW experiment key.
    Historical runs or absent environment opt-in are unaffected.
    """
    enabled = os.environ.get("SK_PHYSICAL_EXPERIMENT_KEY", "").strip()
    if not enabled or enabled != experiment_key:
        return None
    if enabled == "source-daywork-v2":
        raise ValueError("Refusing to enable physical V3 on original experiment")

    paths = _source_files()
    mapping, facilities = _load_source(paths["mapping"], paths["facilities"])
    db_berths = {b["berth_id"] for b in berths}
    if db_berths != set(EXPECTED_MAPPING):
        raise ValueError(f"Physical V3 requires 8 existing DB berths: {sorted(db_berths)}")
    ids = [c["call_id"] for c in calls]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate call IDs in frozen snapshot")
    all_profiles = read_profiles(paths["profiles"])
    missing = sorted(set(ids) - set(all_profiles))
    if missing:
        raise ValueError(f"VESSEL_PROFILE_MISSING: {len(missing)} active calls: {missing[:15]}")
    profiles = {cid: all_profiles[cid] for cid in ids}
    audit = audit_calls(mapping, facilities, profiles, ids)
    blocked = [c for c in audit["calls"] if c["status"] != "READY"]
    if blocked:
        message = [c["call_id"] for c in blocked[:25]]
        raise ValueError(
            f"NO_COMPATIBLE_BERTH_IN_MODEL: {len(blocked)} active SYNTHETIC calls; "
            f"IDs={message}; run scripts/audit_sk_physical_v3.py to inspect each facility rejection"
        )
    eligible = {c["call_id"]: sorted(c["eligible_berths"]) for c in audit["calls"]}
    return {
        "policy": POLICY,
        "scope": "SINGLE_BERTH_DOCUMENTED_PHYSICAL_ONLY_NOT_OPERATIONAL_APPROVAL",
        "data_origin": "SYNTHETIC",
        "synthetic_call_count": len(ids),
        "facility_map": mapping,
        "eligible_berths": eligible,
        "profiles": profiles,
        "source_files": {key: fingerprint(path) for key, path in paths.items()},
        "not_evaluated": audit["not_evaluated"],
        "multi_berth_occupancy": "NOT_MODELED",
    }


def verify_sk_snapshot(physical):
    if not isinstance(physical, dict) or physical.get("policy") != POLICY:
        raise ValueError("Unknown SK physical snapshot policy")
    if physical.get("data_origin") != "SYNTHETIC":
        raise ValueError("SK V3 requires SYNTHETIC provenance")
    if physical.get("facility_map") != EXPECTED_MAPPING:
        raise ValueError("Unexpected SK facility mapping in snapshot")
    for key in ("mapping", "facilities", "profiles"):
        stored = physical["source_files"][key]
        actual = fingerprint(stored["path"])
        if actual != stored:
            raise ValueError(f"Frozen SK {key} file modified: {stored['path']}")


def eligible_berths(snapshot, call_id, available_berths):
    """Legacy snapshots: pass-through. Opted-in snapshots: strict whitelist."""
    physical = snapshot.get("sk_physical")
    active = list(available_berths)
    if physical is None:
        return active
    if physical.get("policy") != POLICY:
        raise ValueError("Unsupported SK physical policy")
    valid = physical["eligible_berths"].get(call_id)
    if not valid:
        raise ValueError(f"NO_COMPATIBLE_BERTH_IN_MODEL: {call_id}")
    return [berth for berth in active if berth in valid]


def assert_eligible(snapshot, call_id, berth_id):
    if snapshot.get("sk_physical") is None:
        return
    if berth_id not in eligible_berths(snapshot, call_id, [berth_id]):
        raise ValueError(f"INCOMPATIBLE_BERTH: {call_id} on {berth_id}")
