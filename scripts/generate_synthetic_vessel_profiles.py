#!/usr/bin/env python3
"""Generate explicitly SYNTHETIC ship data, never bound to a preselected berth.

By default --mode independent (A: strict audit). An optional --mode conditioned
(B: rejection sampling against ANY of the 8 physical berths, not any specific
berth) is available for *future* scenario design. The validator always stays
strict; neither mode changes original contracts, calls, or PostgreSQL.

All dimensional ranges, size-class weights, cargo rates, and GT ratio below
are artificial scenario assumptions, NOT facts about SK or vessels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.daily_planning.berth_rules import (
    load_facilities, load_mapping, validate_mapping,
    check_call_against_virtual_berth,
)

# Synthetic assumptions only; these are NOT SK operating limits.
SIZE_CLASSES = {
    "SMALL": {"dwt": (4000, 12000), "loa_m": (85, 135), "draft_m": (5.0, 9.0)},
    "MEDIUM": {"dwt": (12000, 70000), "loa_m": (130, 245), "draft_m": (7.0, 14.0)},
    "LARGE": {"dwt": (70000, 160000), "loa_m": (230, 285), "draft_m": (12.0, 17.0)},
}
CARGO_TYPES = ("CRUDE", "REFINED_PRODUCT", "LPG")
EXPECTED_MAPPING = {
    "BERTH-01": "SK#1-1", "BERTH-02": "SK#2-7", "BERTH-03": "SK#3",
    "BERTH-04": "SK#4-1", "BERTH-05": "SK#5-5", "BERTH-06": "SK#6",
    "BERTH-07": "SK#7", "BERTH-08": "SK#8",
}


def _stable_random(seed: int, call_id: str, attempt: int):
    bits = hashlib.sha256(f"{seed}|{call_id}|{attempt}|synthetic-v3".encode()).digest()
    return random.Random(int.from_bytes(bits[:16], "big"))


def make_profile(call_id: str, seed: int, attempt: int = 0):
    rng = _stable_random(seed, call_id, attempt)
    group = rng.choices(("SMALL", "MEDIUM", "LARGE"), weights=(45, 40, 15), k=1)[0]
    bounds = SIZE_CLASSES[group]
    dwt = rng.randint(*bounds["dwt"])
    loa_m = round(rng.uniform(*bounds["loa_m"]), 2)
    draft_m = round(rng.uniform(*bounds["draft_m"]), 2)
    gt = max(1, int(dwt * rng.uniform(0.52, 0.64)))
    vessel_hash = hashlib.sha256(f"SYNTHETIC|{seed}|{call_id}".encode()).hexdigest()[:12].upper()
    return {
        "call_id": call_id,
        "vessel_id": "SYN-VSL-" + vessel_hash,
        "draft_m": draft_m,
        "dwt": dwt,
        "loa_m": loa_m,
        "gt": gt,
        "cargo_type": rng.choice(CARGO_TYPES),
        "data_origin": "SYNTHETIC",
    }


def _eligible_count(profile, mapping, facilities):
    return sum(
        check_call_against_virtual_berth(profile, bid, mapping, facilities)["status"] == "PASS"
        for bid in sorted(mapping)
    )


def generate(call_ids, *, seed=42, mode="independent", mapping=None, facilities=None, max_attempts=512):
    """Return generator payload; never silently omit or mutate an input call."""
    ids = list(call_ids)
    if not ids or len(ids) != len(set(ids)) or any(
        not isinstance(cid, str) or not cid.strip() or cid != cid.strip() for cid in ids
    ):
        raise ValueError("call_ids must be unique nonblank strings")
    if mode not in ("independent", "conditioned"):
        raise ValueError("mode must be independent or conditioned")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    mapping = mapping if mapping is not None else load_mapping()
    facilities = facilities if facilities is not None else load_facilities()
    problems = validate_mapping(mapping, facilities)
    if problems or mapping != EXPECTED_MAPPING:
        raise ValueError(f"Expected exactly approved SK piers 1-8 mapping: {problems}")
    rows = []
    attempted = 0
    rejected = 0
    for cid in sorted(ids):
        for attempt in range(max_attempts if mode == "conditioned" else 1):
            profile = make_profile(cid, seed, attempt)
            attempted += 1
            if mode == "independent" or _eligible_count(profile, mapping, facilities) > 0:
                rows.append(profile)
                break
            rejected += 1
        else:
            raise ValueError(f"Unable to generate any singly compatible SYNTHETIC vessel for {cid}; "
                             "do not expand limits without documenting scenario assumptions")
    return {
        "schema_version": 1,
        "purpose": "SYNTHETIC_SCENARIO_ONLY_NOT_REAL_VESSEL_DATA",
        "data_origin": "SYNTHETIC",
        "generator": {
            "name": "berth-independent-synthetic-v3",
            "seed": seed,
            "mode": mode,
            "selection_rule": "stable per-call SHA-256 RNG; no preselected physical berth",
            "conditioning_rule": (
                "NONE; strict preflight may reject the generated scenario" if mode == "independent"
                else "rejection-sample until >=1 SINGLE berth PASS across all 8; no berth preassignment"
            ),
            "sample_attempts": attempted,
            "rejected_attempts": rejected,
            "simulation_assumptions": [
                "All vessel IDs, cargo types, DWT, LOA, draft and GT are SYNTHETIC, not observed",
                "Cargo probabilities and 45/40/15 size weights are arbitrary scenario assumptions",
                "SMALL DWT 4000-12000, LOA 85-135m, draft 5-9m are synthetic ranges",
                "MEDIUM DWT 12000-70000, LOA 130-245m, draft 7-14m are synthetic ranges",
                "LARGE DWT 70000-160000, LOA 230-285m, draft 12-17m are synthetic ranges",
                "GT = 0.52-0.64*DWT is a synthetic placeholder; physical correlation not validated",
                "Size/draft/LOA sampled independently, not verified real-vessel geometry",
                "SK#7/#8 석유저제품 stays unresolved, never normalized to REFINED_PRODUCT",
                "No adjacent/multi-berth occupancy; no tide/weather/tug/tank/pipeline approval",
            ],
        },
        "calls": rows,
    }


def main():
    parser = argparse.ArgumentParser(description="Berth-independent SYNTHETIC vessel generator")
    parser.add_argument("--call-ids-file", type=Path, default=ROOT / "input/call_ids.txt")
    parser.add_argument("--out", type=Path, default=ROOT / "input/vessel_profiles.sk-v3-independent.json")
    parser.add_argument("--expected-count", type=int, default=140)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--mode", choices=("independent", "conditioned"), default="independent")
    parser.add_argument("--max-attempts", type=int, default=512)
    args = parser.parse_args()
    ids = [line.strip() for line in args.call_ids_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(ids) != args.expected_count:
        parser.error(f"Expected {args.expected_count} call IDs but found {len(ids)}; do not invent or delete calls")
    payload = generate(ids, seed=args.seed, mode=args.mode, max_attempts=args.max_attempts)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:  # never overwrite existing synthetic input
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"CREATED {len(payload['calls'])} EXPLICITLY SYNTHETIC profiles: {args.out}")
    print(f"Mode: {args.mode}; rejected generations: {payload['generator']['rejected_attempts']}")
    print("SYNTHETIC CARGO COUNTS:", dict(sorted(Counter(p['cargo_type'] for p in payload['calls']).items())))
    print("No source contract, call, historical result or PostgreSQL modification")


if __name__ == "__main__":
    main()
