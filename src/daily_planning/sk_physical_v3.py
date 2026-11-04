
"""Opt-in SK physical berth compatibility for synthetic experiment v3."""

import json
from pathlib import Path

from .berth_rules import (
    load_facilities,
    load_mapping,
    validate_mapping,
    check_call_against_virtual_berth,
    NOT_EVALUATED,
)
from .input_loader import fingerprint

ROOT = Path(__file__).resolve().parents[2]

EXPERIMENT_KEY = "sk-physical-v3"
PROFILES_PATH = ROOT / "input/vessel_profiles.sk-v3-independent.json"


def build_physical_context(active_calls):
    """Build a frozen, auditable compatibility matrix."""

    mapping = load_mapping()
    facilities = load_facilities()

    problems = validate_mapping(mapping, facilities)
    if problems:
        raise ValueError("SK berth mapping invalid: " + "; ".join(problems))

    raw = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))

    if raw.get("schema_version") != 1:
        raise ValueError("Unsupported synthetic vessel profile version")

    if raw.get("data_origin") != "SYNTHETIC":
        raise ValueError("Vessel profiles must declare SYNTHETIC origin")

    # Accept the independent generator format.
    rows = raw.get("profiles")
    if not isinstance(rows, list):
        raise ValueError("Expected profiles array")

    profiles = {}
    for row in rows:
        cid = row.get("call_id")
        if not isinstance(cid, str) or not cid:
            raise ValueError("Missing call_id in profile")
        if cid in profiles:
            raise ValueError(f"Duplicate vessel profile: {cid}")
        if row.get("data_origin") != "SYNTHETIC":
            raise ValueError(f"Profile is not SYNTHETIC: {cid}")
        if row.get("target_berth_id") is not None:
            raise ValueError(f"Profile has a preselected berth: {cid}")
        profiles[cid] = row

    eligible = {}
    selected_profiles = {}
    blocked = []

    for call in active_calls:
        cid = call["call_id"]
        profile = profiles.get(cid)

        if profile is None:
            blocked.append(cid + ": VESSEL_PROFILE_MISSING")
            continue

        selected_profiles[cid] = profile
        passed = []

        for berth_id in sorted(mapping):
            result = check_call_against_virtual_berth(
                profile, berth_id, mapping, facilities
            )
            if result["status"] == "PASS":
                passed.append(berth_id)

        eligible[cid] = passed

        if not passed:
            blocked.append(cid + ": NO_COMPATIBLE_BERTH")

    if blocked:
        raise ValueError(
            "SK physical preflight blocked "
            f"{len(blocked)} call(s): "
            + ", ".join(blocked[:20])
        )

    return {
        "mode": "DOCUMENTED_PHYSICAL_COMPATIBILITY_V1",
        "data_origin": "SYNTHETIC",
        "mapping": mapping,
        "profiles": selected_profiles,
        "eligible_berths_by_call": eligible,
        "not_evaluated": list(NOT_EVALUATED),
        "source_files": {
            "mapping": fingerprint(ROOT / "config/berth_mapping.json"),
            "facilities": fingerprint(ROOT / "data/sk_facilities.json"),
            "profiles": fingerprint(PROFILES_PATH),
        },
    }


def is_compatible(snapshot, call_id, berth_id):
    physical = snapshot.get("sk_physical")

    # Preserve legacy behavior for every existing experiment.
    if physical is None:
        return True

    return berth_id in physical["eligible_berths_by_call"].get(
        call_id, []
    )


def require_compatible(snapshot, call_id, berth_id):
    if not is_compatible(snapshot, call_id, berth_id):
        raise ValueError(
            f"INCOMPATIBLE_BERTH: call={call_id}, berth={berth_id}"
        )


def verify_physical_files(physical):
    for name, saved in physical["source_files"].items():
        if fingerprint(saved["path"]) != saved:
            raise ValueError(
                f"SK physical input changed after snapshot: {name}"
            )
