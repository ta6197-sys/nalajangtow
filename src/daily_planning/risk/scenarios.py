"""Stage 1: reproducible *synthetic* exogenous shock scenarios.

This module does not change NOR, rewrite the weather CSV, assign berths,
or claim a probability calibrated to actual Ulsan conditions.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path

import numpy as np


def load_scenario_config(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_scenario_config(data)
    return data


def _distribution(config: Mapping[str, object], key: str, *, nonnegative: bool = False):
    raw = config.get(key)
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"Missing/empty distribution: {key}")
    values, probabilities = [], []
    for value, prob in sorted(raw.items(), key=lambda item: Decimal(str(item[0]))):
        d = Decimal(str(value))
        p = Decimal(str(prob))
        if not (d.is_finite() and p.is_finite()):
            raise ValueError(f"Nonfinite distribution: {key}")
        if p < 0 or (nonnegative and d < 0):
            raise ValueError(f"Negative parameter or probability: {key}")
        if key != "work_duration_multiplier" and d != d.to_integral_value():
            raise ValueError(f"Minutes must be whole numbers: {key}")
        if key == "work_duration_multiplier" and d <= 0:
            raise ValueError("Work duration multipliers must be positive")
        values.append(float(d) if key == "work_duration_multiplier" else int(d))
        probabilities.append(float(p))
    if abs(sum(Decimal(str(p)) for p in raw.values()) - 1) > Decimal("1e-10"):
        raise ValueError(f"Distribution weights must sum to 1: {key}")
    return values, probabilities


def validate_scenario_config(config: Mapping[str, object]) -> None:
    if config.get("schema_version") != 1 or config.get("provenance") != "SYNTHETIC_ASSUMPTION":
        raise ValueError("Only explicitly SYNTHETIC schema_version=1 accepted")
    if not isinstance(config.get("model_id"), str) or not config["model_id"]:
        raise ValueError("model_id required")
    if type(config.get("seed")) is not int or config["seed"] < 0:
        raise ValueError("Seed must be nonnegative int")
    if type(config.get("sample_count")) is not int or not 1 <= config["sample_count"] <= 100000:
        raise ValueError("sample_count must be 1..100000")
    for field in ("arrival_shift_minutes", "work_duration_multiplier",
                  "additional_weather_block_minutes", "weather_block_onset_offset_minutes"):
        _distribution(config, field, nonnegative=field != "arrival_shift_minutes")
    blocks = config.get('weather_blocks', [])
    if not isinstance(blocks, list) or len(blocks) > 16:
        raise ValueError('weather_blocks must be a list of at most 16 distributions')
    for index, block in enumerate(blocks):
        if not isinstance(block, dict) or set(block) != {'duration_minutes', 'onset_offset_minutes'}:
            raise ValueError(f'Invalid weather block distribution {index}')
        _distribution(block, 'duration_minutes', nonnegative=True)
        _distribution(block, 'onset_offset_minutes', nonnegative=True)


def generate_scenarios(config: Mapping[str, object], call_ids: Sequence[str]) -> list[dict]:
    """One shared weather shock per scenario; per-call arrival/work shocks.

    Sorting IDs ensures input order does not alter draws. Identical config,
    calls and seed produce identical scenario lists. All candidates must
    reuse one generated list; never generate separately per candidate.
    """
    validate_scenario_config(config)
    if any(not isinstance(cid,str) or not cid for cid in call_ids):
        raise ValueError("call_ids must be nonempty strings")
    ids = sorted(call_ids)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Need unique, nonempty call IDs")
    config_bytes = json.dumps(dict(config), sort_keys=True, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
    fingerprint = hashlib.sha256(config_bytes).hexdigest()[:12]
    distributions = {
        key: _distribution(config,key)
        for key in ("arrival_shift_minutes", "work_duration_multiplier",
                    "additional_weather_block_minutes", "weather_block_onset_offset_minutes")
    }
    block_distributions = [(
        _distribution(block, 'duration_minutes', nonnegative=True),
        _distribution(block, 'onset_offset_minutes', nonnegative=True))
        for block in config.get('weather_blocks', [])]
    rng = np.random.default_rng(config["seed"])
    scenarios = []
    for idx in range(config["sample_count"]):
        size_values, size_probs = distributions["additional_weather_block_minutes"]
        onset_values, onset_probs = distributions["weather_block_onset_offset_minutes"]
        weather_minutes = int(rng.choice(size_values, p=size_probs))
        onset_minutes = int(rng.choice(onset_values, p=onset_probs)) if weather_minutes else None
        call_shocks = {}
        arrival_values, arrival_probs = distributions["arrival_shift_minutes"]
        work_values, work_probs = distributions["work_duration_multiplier"]
        for cid in ids:
            call_shocks[cid] = {
                "available_at_shift_minutes": int(rng.choice(arrival_values, p=arrival_probs)),
                "work_duration_multiplier": float(rng.choice(work_values, p=work_probs)),
            }
        added_blocks = []
        for (durations, duration_probs), (onsets, onset_probs) in block_distributions:
            duration = int(rng.choice(durations, p=duration_probs))
            if duration:
                added_blocks.append(dict(duration_minutes=duration,
                    onset_offset_minutes=int(rng.choice(onsets, p=onset_probs))))
        entry = {
            "scenario_id": f"{config['model_id']}-{fingerprint}-{idx:06d}",
            "scenario_index": idx,
            "scenario_weight": f"{Decimal(1) / Decimal(config['sample_count']):.15f}",
            "provenance": "SYNTHETIC_ASSUMPTION",
            "additional_weather_block_minutes": weather_minutes,
            "weather_block_onset_offset_minutes": onset_minutes,
            "call_shocks": call_shocks,
        }
        if 'weather_blocks' in config:
            entry['weather_blocks'] = added_blocks
        scenarios.append(entry)
    return scenarios
