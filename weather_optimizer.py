"""Experiment-only CLX weather / berth scheduler integration.

This module does NOT claim SK operating limits or replace a verified contract engine.
It reads the existing model_input.json structure and the supplied 30-minute
weather CSV after they have been copied into the same project workspace.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

SLOT = timedelta(minutes=30)
KST = timezone(timedelta(hours=9))
SUPPORTED_DAY_MODES = {"SHINC", "SHEX_SUN", "SHEX_WEEKEND", "SHEX_SAT12"}
SUPPORTED_WEATHER_POLICIES = {"COUNT", "EXCLUDE", "HALF"}
SUPPORTED_POST_RULES = {"NONE", "OWNER_ONLY", "OWNER_AND_TERMINAL"}


def at_datetime(value: str | datetime) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
    if dt.tzinfo is not None:
        dt = dt.astimezone(KST).replace(tzinfo=None)
    if dt.second or dt.microsecond:
        raise ValueError(f"Sub-minute timestamps are not supported: {value}")
    return dt


@dataclass(frozen=True)
class WeatherRule:
    wind_limit_mps: float
    wave_limit_m: float
    wave_column: str
    weather_scope: str  # WORK_ONLY or CLOCK_WINDOW
    after_end: str      # ERROR or CYCLE (requires explicit approval)

    @classmethod
    def from_file(cls, path: Path):
        raw = json.loads(path.read_text(encoding="utf-8"))
        missing = [k for k in ("wind_limit_mps", "wave_limit_m", "wave_column", "weather_scope", "after_end") if raw.get(k) is None]
        if missing or not raw.get("approved_for_run", False):
            raise ValueError("Operating assumptions are not approved: " + ", ".join(missing or ["approved_for_run"]))
        if raw["weather_scope"] not in {"WORK_ONLY", "CLOCK_WINDOW"}:
            raise ValueError("weather_scope must be WORK_ONLY or CLOCK_WINDOW")
        if raw["after_end"] not in {"ERROR", "CYCLE"}:
            raise ValueError("after_end must be ERROR or CYCLE")
        if raw["wave_column"] not in {"유의파고(m)", "최대파고(m)", "평균파고(m)"}:
            raise ValueError("Unknown wave column")
        for k in ("wind_limit_mps", "wave_limit_m"):
            if not isinstance(raw[k], (int, float)) or raw[k] < 0:
                raise ValueError(f"Invalid {k}")
        return cls(**{k: raw[k] for k in ("wind_limit_mps", "wave_limit_m", "wave_column", "weather_scope", "after_end")})


class MissingWeather(ValueError):
    pass


class WeatherSeries:
    """Time grid: one row covers [source_time, source_time + 30 minutes)."""

    def __init__(self, rows: list[dict[str, Any]], *, target_year: int | None = None):
        if not rows:
            raise ValueError("No weather rows")
        parsed = []
        for r in rows:
            if "일시" not in r:
                raise ValueError("CSV lacks 일시 column")
            src = at_datetime(r["일시"])
            dst = src.replace(year=target_year) if target_year is not None else src
            try:
                wind = float(r["풍속(m/s)"])
                wave = {name: float(r[name]) for name in ("유의파고(m)", "최대파고(m)", "평균파고(m)")}
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError(f"Missing/invalid wind or wave at source time {src}") from exc
            parsed.append((dst, src.isoformat(), wind, wave))
        parsed.sort(key=lambda item: item[0])
        for left, right in zip(parsed, parsed[1:]):
            if right[0] - left[0] != SLOT:
                raise ValueError(f"Weather grid gap/duplicate: {left[0]} -> {right[0]}")
        self.rows = parsed
        self.first = parsed[0][0]
        self.end = parsed[-1][0] + SLOT
        self.total = len(parsed)
        self.target_year = target_year

    @classmethod
    def from_csv(cls, path: Path, target_year: int | None = None):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return cls(list(csv.DictReader(handle)), target_year=target_year)

    def slot(self, at: datetime, rule: WeatherRule):
        if at < self.first:
            raise MissingWeather(f"Weather not available before {self.first}: requested {at}")
        absolute_idx = int((at - self.first) // SLOT)
        if absolute_idx >= self.total and rule.after_end == "ERROR":
            raise MissingWeather(f"Weather ends at {self.end}; required time {at}. Supply more data or explicitly approve CYCLE simulation.")
        idx = absolute_idx % self.total
        slot_start = self.first + absolute_idx * SLOT
        datum = self.rows[idx]
        return slot_start, slot_start + SLOT, datum

    def blocked(self, at: datetime, rule: WeatherRule) -> bool:
        _, _, datum = self.slot(at, rule)
        return datum[2] > rule.wind_limit_mps or datum[3][rule.wave_column] > rule.wave_limit_m

    def first_workable(self, start: datetime, rule: WeatherRule) -> datetime:
        at = start
        # At most one weather cycle; if nothing is workable, fail fast.
        seen = 0
        while self.blocked(at, rule):
            _, end, _ = self.slot(at, rule)
            at = end
            seen += 1
            if seen > self.total:
                raise ValueError("No workable weather slot found within one weather cycle")
        return at


def simulate_work(start: datetime, required_minutes: int, weather: WeatherSeries, rule: WeatherRule):
    if required_minutes <= 0:
        raise ValueError("Processing minutes must be positive")
    work_start = weather.first_workable(start, rule)
    at = work_start
    left = int(required_minutes)
    pauses: list[tuple[datetime, datetime]] = []
    while left > 0:
        _, end, _ = weather.slot(at, rule)
        length = int((end - at).total_seconds() // 60)
        if length <= 0:
            raise ValueError("No forward progress")
        if weather.blocked(at, rule):
            if pauses and pauses[-1][1] == at:
                pauses[-1] = pauses[-1][0], end
            else:
                pauses.append((at, end))
            at = end
        else:
            consumed = min(left, length)
            left -= consumed
            at += timedelta(minutes=consumed)
    return work_start, at, pauses


def day_allowed(at: datetime, mode: str, holidays: set[str]) -> bool:
    if mode not in SUPPORTED_DAY_MODES:
        raise ValueError(f"Unsupported day_mode: {mode}")
    if mode == "SHINC":
        return True
    if at.date().isoformat() in holidays:
        return False
    weekday = at.weekday()
    if mode == "SHEX_SUN":
        return weekday != 6
    if mode == "SHEX_WEEKEND":
        return weekday < 5
    return weekday < 5 or (weekday == 5 and at.hour < 12)


def next_day_boundary(at: datetime, mode: str) -> datetime:
    midnight = datetime(at.year, at.month, at.day) + timedelta(days=1)
    if mode == "SHEX_SAT12" and at.weekday() == 5 and at.hour < 12:
        return min(midnight, at.replace(hour=12, minute=0))
    return midnight


def _holidays(contract: dict) -> set[str]:
    value = contract.get("holidays", [])
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError("holidays must be a list")
    return set(str(x)[:10] for x in value)


def _is_work_pause(at: datetime, pauses: list[tuple[datetime, datetime]]) -> tuple[bool, datetime | None]:
    # Small list, but no per-minute scanning; return upcoming segment boundary.
    for start, end in pauses:
        if at < start:
            return False, start
        if start <= at < end:
            return True, end
    return False, None


def price_contract(
    contract: dict,
    work_start: datetime,
    finish: datetime,
    pauses: list[tuple[datetime, datetime]],
    weather: WeatherSeries,
    rule: WeatherRule,
    exception_intervals=None,
) -> dict:
    """Price the scenario timeline using the validated supplied contract rules.

    WORK_ONLY excludes only actual simulated work pauses. CLOCK_WINDOW uses the
    full contract window. Owner/terminal exceptions are applied only when an
    explicit scenario interval is supplied; historical SOF stops are not copied.
    """
    from contracts import contract_clock_start, settle_intervals
    work_start, finish = at_datetime(work_start), at_datetime(finish)
    effective = contract_clock_start(contract, work_start)
    interruptions=[]
    if rule.weather_scope == 'WORK_ONLY':
        interruptions.extend((at_datetime(a),at_datetime(b),'WEATHER') for a,b in pauses)
    elif rule.weather_scope == 'CLOCK_WINDOW':
        at=effective
        while at<finish:
            _,boundary,_=weather.slot(at,rule)
            end=min(finish,boundary)
            if weather.blocked(at,rule):interruptions.append((at,end,'WEATHER'))
            at=end
    else:
        raise ValueError('Unsupported weather scope')
    for a,b,code in exception_intervals or []:
        if code not in {'OWNER_BREAKDOWN','TERMINAL_STOP'}:
            raise ValueError(f'Unsupported scenario interruption: {code}')
        interruptions.append((at_datetime(a),at_datetime(b),code))
    interruptions=sorted((max(a,effective),min(b,finish),code) for a,b,code in interruptions if b>a and b>effective and a<finish)
    events=[];at=effective
    for a,b,code in interruptions:
        if a<at:raise ValueError('Overlapping scenario interruptions require explicit resolution')
        if at<a:events.append((at,a,'WAIT'))
        events.append((a,b,code));at=b
    if at<finish:events.append((at,finish,'WAIT'))
    return settle_intervals(contract,effective,finish,events)


def load_model(path: Path) -> tuple[list[dict], list[str]]:
    from contracts import validate_contract
    raw = json.loads(path.read_text(encoding="utf-8"))
    calls = raw.get("calls", [])
    if not calls:
        raise ValueError("No calls in model_input.json")
    ids = [str(x["call_id"]) for x in calls]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate call_id")
    for c in calls:
        validate_contract(c['contract'])
        if c.get("processing_minutes") is None or int(c["processing_minutes"]) <= 0:
            raise ValueError(f"Missing WORK minutes: {c['call_id']}")
        if int(c["processing_minutes"]) != c["processing_minutes"]:
            raise ValueError(f"Non-integer minute processing time: {c['call_id']}")
        if at_datetime(c["available_at"]) != at_datetime(c["contract"]["nor_at"]):
            raise ValueError(f"NOR not equal to availability for {c['call_id']}")
    berths = raw.get("berths", [])
    berth_ids = [str(b.get("berth_id", b.get("id", b))) if isinstance(b, dict) else str(b) for b in berths]
    if not berth_ids or any(not bid for bid in berth_ids) or len(set(berth_ids)) != len(berth_ids):
        raise ValueError("Expected nonempty, distinct representative berth IDs")
    return calls, berth_ids


def evaluate_order(calls: list[dict], berth_ids: list[str], order: list[str], weather: WeatherSeries, rule: WeatherRule):
    by_id = {c["call_id"]: c for c in calls}
    rank = {cid: ix for ix, cid in enumerate(order)}
    if set(rank) != set(by_id) or len(rank) != len(by_id):
        raise ValueError("Priority order must contain each call once")
    base_time = min(at_datetime(c["available_at"]) for c in calls)
    berth_free = {bid: base_time for bid in berth_ids}
    pending = set(by_id)
    rows = []
    while pending:
        berth = min(berth_ids, key=lambda b: (berth_free[b], b))
        berth_time = berth_free[berth]
        available = [cid for cid in pending if at_datetime(by_id[cid]["available_at"]) <= berth_time]
        if not available:
            next_avail = min(at_datetime(by_id[cid]["available_at"]) for cid in pending)
            berth_time = max(berth_time, next_avail)
            available = [cid for cid in pending if at_datetime(by_id[cid]["available_at"]) <= berth_time]
        cid = min(available, key=lambda x: rank[x])
        call = by_id[cid]
        proposed = max(berth_time, at_datetime(call["available_at"]))
        work_start, finish, pauses = simulate_work(proposed, int(call["processing_minutes"]), weather, rule)
        cost = price_contract(call["contract"], work_start, finish, pauses, weather, rule)
        rows.append({"call_id": cid, "berth_id": berth, "nor_at": at_datetime(call["available_at"]).isoformat(),
                     "work_start_at": work_start.isoformat(), "completion_at": finish.isoformat(),
                     "weather_pause_minutes": sum(int((y-x).total_seconds()/60) for x,y in pauses),
                     "weather_pause_intervals": [[x.isoformat(), y.isoformat()] for x,y in pauses], **cost})
        pending.remove(cid)
        berth_free[berth] = finish
    _validate_schedule(rows, calls)
    return {"total_demurrage_cents": sum(r["demurrage_cents"] for r in rows), "schedule": rows}


def _validate_schedule(rows: list[dict], calls: list[dict]):
    if len(rows) != len(calls):
        raise AssertionError("Incomplete schedule")
    if {r['call_id'] for r in rows} != {c['call_id'] for c in calls}:
        raise AssertionError('Missing or duplicate call IDs in schedule')
    grouped = {}
    for r in rows:
        start = at_datetime(r["work_start_at"])
        finish = at_datetime(r["completion_at"])
        if start < at_datetime(r["nor_at"]) or finish <= start:
            raise AssertionError(f"Invalid call timing: {r['call_id']}")
        grouped.setdefault(r["berth_id"], []).append((start, finish))
    for berth, times in grouped.items():
        times.sort()
        for (_, last_end), (next_start, _) in zip(times, times[1:]):
            if next_start < last_end:
                raise AssertionError(f"Berth conflict: {berth}")


def search_schedules(calls: list[dict], berth_ids: list[str], weather: WeatherSeries, rule: WeatherRule, trials: int, seed: int):
    rng = random.Random(seed)
    priority = [c["call_id"] for c in sorted(calls, key=lambda c: (at_datetime(c["available_at"]), c["call_id"]))]
    baseline = evaluate_order(calls, berth_ids, priority, weather, rule)
    incumbent = baseline
    best_order = priority[:]
    n = len(priority)
    candidates = [("FCFS", baseline)]
    tested = [("FCFS", baseline)]
    for trial in range(trials):
        if n < 2:
            break
        proposal = best_order[:]
        i, j = rng.sample(range(n), 2)
        proposal[i], proposal[j] = proposal[j], proposal[i]
        candidate = evaluate_order(calls, berth_ids, proposal, weather, rule)
        tested.append((f"SWAP_{trial+1}", candidate))
        if candidate["total_demurrage_cents"] < incumbent["total_demurrage_cents"]:
            incumbent = candidate
            best_order = proposal
            candidates.append((f"SWAP_{trial+1}", candidate))
    results = [{"name": name, "total_demurrage_cents": r["total_demurrage_cents"],
                "schedule": r["schedule"]} for name, r in candidates]
    distinct = []
    seen = set()
    for name, r in sorted(tested, key=lambda item: (item[1]["total_demurrage_cents"], item[0])):
        signature = tuple(sorted((x["call_id"], x["berth_id"], x["work_start_at"]) for x in r["schedule"]))
        if signature not in seen:
            distinct.append({"name": name, "total_demurrage_cents": r["total_demurrage_cents"], "schedule": r["schedule"]})
            seen.add(signature)
        if len(distinct) == 3:
            break
    return {"type": "HEURISTIC_BEST_AMONG_SAMPLED_NOT_PROVEN_GLOBAL_OPTIMUM", "baseline": baseline,
            "best": incumbent, "top_three_candidates": distinct, "selected_candidate_history": results,
            "trials": trials, "seed": seed, "saving_cents": baseline["total_demurrage_cents"] - incumbent["total_demurrage_cents"]}


def main():
    parser = argparse.ArgumentParser(description="Isolated CLX weather-coupled berth heuristic")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--weather", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-year", type=int, default=2026)
    parser.add_argument("--trials", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    rule = WeatherRule.from_file(args.config)
    calls, berths = load_model(args.model)
    series = WeatherSeries.from_csv(args.weather, target_year=args.target_year)
    result = search_schedules(calls, berths, series, rule, args.trials, args.seed)
    result["metadata"] = {"source_model": str(args.model), "weather_csv": str(args.weather),
                          "weather_original_first": series.rows[0][1],
                          "weather_replay_start": series.first.isoformat(), "weather_replay_end_exclusive": series.end.isoformat(),
                          "thresholds": rule.__dict__, "weather_is_historical_replay_not_forecast": True,
                          "contract_calculator": "EXPERIMENTAL_ONLY__COMPARE_TO_EXISTING_VERIFIED_CALCULATOR"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"cases": len(calls), "berths": len(berths), "base_cents": result["baseline"]["total_demurrage_cents"],
                      "best_cents": result["best"]["total_demurrage_cents"], "saved_cents": result["saving_cents"],
                      "not_proven_optimal": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
