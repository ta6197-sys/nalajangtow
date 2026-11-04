"""SK CLX physical berth compatibility checks (stage 1, no DB writes).

This checks documented dimensions/cargo only. PASS never means safe/approved
mooring; night/weather/tide/berthing/unberthing/tugs/tanks are NOT evaluated.
Source: user-supplied 2026-10-28 workbook, normalized data/sk_facilities.json.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
FACILITIES_FILE = ROOT / "data" / "sk_facilities.json"
MAPPING_FILE = ROOT / "config" / "berth_mapping.json"

# Controlled aliases only. Unknown raw source tokens are never auto-corrected.
CARGO_ALIASES = {
    "원유": "CRUDE", "CRUDE": "CRUDE",
    "석유제품": "REFINED_PRODUCT", "석유정제품": "REFINED_PRODUCT",
    "REFINED_PRODUCT": "REFINED_PRODUCT",
    "LPG": "LPG",
    "케미칼": "CHEMICAL", "케미칼류": "CHEMICAL", "CHEMICAL": "CHEMICAL",
}
NOT_EVALUATED = [
    "MOORING_TIME_WINDOW", "UNMOORING_TIME_WINDOW", "BERTHING_WEATHER",
    "CARGO_WORK_WEATHER", "TIDE_UKC_DYNAMIC", "TUG_PILOT_RESOURCES",
    "ADJACENT_BERTH_INTERFERENCE", "TANK_PIPELINE_CAPACITY",
    "ACTUAL_PORT_APPROVAL",
]


def load_facilities(path: str | Path = FACILITIES_FILE) -> dict[str, dict]:
    """Load document-based SK facilities; UPA-only unverified rows were excluded."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported SK facility data version")
    facilities = payload.get("facilities")
    if not isinstance(facilities, list) or not facilities:
        raise ValueError("No SK facility data")
    output = {}
    for row in facilities:
        code = row.get("facility_code")
        if not isinstance(code, str) or not code.startswith(("SKB#", "SK#")):
            raise ValueError(f"Unsupported SK facility code: {code}")
        if code in output:
            raise ValueError(f"Duplicate SK facility: {code}")
        output[code] = row
    return output


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean cannot be used as dimension")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"Invalid numeric input: {value}") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"Dimension must be finite and non-negative: {value}")
    return number


def _cargo_token(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return CARGO_ALIASES.get(value.strip().upper()) or CARGO_ALIASES.get(value.strip())


def _source_cargo(profile: Mapping[str, Any]) -> tuple[set[str], list[str]]:
    value = profile.get("cargo_text_source")
    if not isinstance(value, str) or not value.strip():
        return set(), ["(missing source cargo text)"]
    recognized, unknown = set(), []
    for part in value.split(","):
        word = part.strip()
        token = _cargo_token(word)
        if token:
            recognized.add(token)
        elif word:
            unknown.append(word)
    return recognized, unknown


def check_physical_compatibility(
    vessel: Mapping[str, Any], facility: Mapping[str, Any]
) -> dict[str, Any]:
    """Return PASS/FAIL/UNKNOWN for only physical constraints in SK source.

    Required vessel keys where source has limit: draft_m, dwt, loa_m.
    Cargo: cargo_type. This stage deliberately does not imply global safety.
    """
    issues: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    def record(status: str, code: str, actual: Any, limit: Any, unit: str):
        item = {"status": status, "code": code, "actual": actual, "limit": limit, "unit": unit}
        details.append(item)
        if status != "PASS":
            issues.append(item)

    limits = [
        ("draft_m", "max_draft_m", "MAX_DRAFT", "max", "m"),
        ("dwt", "min_dwt", "MIN_DWT", "min", "DWT"),
        ("dwt", "max_dwt", "MAX_DWT", "max", "DWT"),
        ("loa_m", "min_loa_m", "MIN_LOA", "min", "m"),
        ("loa_m", "max_loa_m", "MAX_LOA", "max", "m"),
    ]
    for field, threshold, code, direction, unit in limits:
        limit = _decimal(facility.get(threshold))
        if limit is None:
            continue  # Source does not state a limit: no invented rule.
        value = _decimal(vessel.get(field))
        if value is None:
            record("UNKNOWN", f"{code}_INPUT_MISSING", None, str(limit), unit)
        elif (value > limit if direction == "max" else value < limit):
            record("FAIL", f"{code}_VIOLATED", str(value), str(limit), unit)
        else:
            record("PASS", f"{code}_OK", str(value), str(limit), unit)

    allowed, unrecognized = _source_cargo(facility)
    supplied_cargo = vessel.get("cargo_type")
    actual_cargo = _cargo_token(supplied_cargo)
    if not actual_cargo:
        record("UNKNOWN", "CARGO_INPUT_UNKNOWN", supplied_cargo, sorted(allowed), "cargo")
    elif actual_cargo in allowed:
        record("PASS", "CARGO_OK", actual_cargo, sorted(allowed), "cargo")
    elif unrecognized:
        record("UNKNOWN", "CARGO_SOURCE_AMBIGUOUS", actual_cargo, unrecognized, "cargo")
    else:
        record("FAIL", "CARGO_NOT_ALLOWED", actual_cargo, sorted(allowed), "cargo")

    status = "FAIL" if any(x["status"] == "FAIL" for x in issues) else ("UNKNOWN" if issues else "PASS")
    return {
        "status": status, "scope": "DOCUMENTED_PHYSICAL_COMPATIBILITY_ONLY",
        "facility_code": facility.get("facility_code"),
        "checks": details, "issues": issues,
        "not_evaluated": list(NOT_EVALUATED),
        "source": {
            "sheet": "시설별 제원", "workbook_row": facility.get("workbook_row"),
            "verification_status": facility.get("verification_status"),
            "source_sk": facility.get("source_sk"),
        },
    }


def validate_mapping(mapping: Mapping[str, Any], facilities: Mapping[str, Any]) -> list[str]:
    """Validate explicit unique mapping for the *existing* eight virtual berths."""
    required = {f"BERTH-{i:02d}" for i in range(1, 9)}
    problems = []
    missing = required - set(mapping)
    unexpected = set(mapping) - required
    if missing:
        problems.append(f"Missing berth keys: {', '.join(sorted(missing))}")
    if unexpected:
        problems.append(f"Unexpected berth keys: {', '.join(sorted(unexpected))}")
    for bid in sorted(required & set(mapping)):
        code = mapping[bid]
        if not code:
            problems.append(f"{bid}: mapping not selected")
        elif code not in facilities:
            problems.append(f"{bid}: unknown/non-SK facility {code}")
    selected = [v for k,v in mapping.items() if k in required and v]
    if len(selected) != len(set(selected)):
        problems.append("Physical facility mapped to more than one virtual berth")
    return problems


def load_mapping(path: str | Path = MAPPING_FILE) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Mapping must be a JSON object")
    return payload


def check_call_against_virtual_berth(
    vessel: Mapping[str, Any], berth_id: str,
    mapping: Mapping[str, Any], facilities: Mapping[str, dict]
) -> dict[str, Any]:
    code = mapping.get(berth_id)
    if not code:
        return {"status": "UNKNOWN", "scope": "DOCUMENTED_PHYSICAL_COMPATIBILITY_ONLY",
                "berth_id": berth_id, "issues": [{"status":"UNKNOWN","code":"FACILITY_MAPPING_MISSING"}],
                "not_evaluated":list(NOT_EVALUATED)}
    if code not in facilities:
        raise ValueError(f"Unknown physical facility for {berth_id}: {code}")
    return {"berth_id": berth_id, **check_physical_compatibility(vessel, facilities[code])}
