"""Pure Stage-4 berth occupancy phases (not yet wired into the CP-SAT engine).

Existing work_start_at/completion_at remain CARGO work boundaries.  A separate
berthing start and release end account for berth occupation before and after
cargo operation.  Contract laytime/charges are intentionally untouched.

No numeric duration here is claimed to be an actual SK CLX operational limit.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
TIME_GRID_MINUTES = 30


def _at(value, label):
    if isinstance(value, datetime):
        at = value
    elif isinstance(value, str):
        try:
            at = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{label}: invalid ISO timestamp") from exc
    else:
        raise ValueError(f"{label}: require ISO timestamp or datetime")
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError(f"{label}: timezone offset required")
    if at.second or at.microsecond:
        raise ValueError(f"{label}: whole-minute timestamp required")
    return at.astimezone(KST)


def _duration(value, label):
    if type(value) is not int or value < 0 or value % TIME_GRID_MINUTES:
        raise ValueError(f"{label}: nonnegative integer multiple of 30 minutes required")
    return value


def build_phase_timeline(row, *, berthing_start_at=None,
                         berthing_minutes=0, unberthing_minutes=0,
                         available_at=None):
    """Validate one proposal and expose a berth-occupancy interval.

    Precondition: the cargo work simulator already produced `row` using the
    actual cargo-work start. A future solver integration must generate both
    work and berth timings together rather than moving timestamps after
    optimization. No such integration is performed by this helper.
    """
    if not isinstance(row, dict) or not row.get("call_id") or not row.get("berth_id"):
        raise ValueError("row requires call_id and berth_id")
    berthing = _duration(berthing_minutes, "berthing_minutes")
    unberthing = _duration(unberthing_minutes, "unberthing_minutes")
    work_start = _at(row["work_start_at"], "work_start_at")
    work_end = _at(row["completion_at"], "completion_at")
    if work_end <= work_start:
        raise ValueError("Cargo completion must follow cargo work start")
    if berthing_start_at is None:
        if berthing:
            raise ValueError("Explicit berthing_start_at required for nonzero berthing")
        berth_start = work_start
    else:
        berth_start = _at(berthing_start_at, "berthing_start_at")
    if work_start != berth_start + timedelta(minutes=berthing):
        raise ValueError("Cargo work must start exactly after the berthing phase")
    if available_at is not None and berth_start < _at(available_at, "available_at"):
        raise ValueError("Berthing cannot start before vessel availability")
    release = work_end + timedelta(minutes=unberthing)
    return {
        "call_id": row["call_id"],
        "berth_id": row["berth_id"],
        "berthing_start_at": berth_start.isoformat(),
        "cargo_work_start_at": work_start.isoformat(),
        "cargo_work_end_at": work_end.isoformat(),
        "berth_release_at": release.isoformat(),
        "berthing_minutes": berthing,
        "unberthing_minutes": unberthing,
        "berth_occupied_minutes": int((release - berth_start).total_seconds() // 60),
        "duration_origin": "EXPLICIT_SIMULATION_PARAMETER_NOT_REAL_SK_LIMIT",
    }


def occupancy_overlap(a, b):
    """True when two explicit berth-occupation intervals overlap."""
    if a["berth_id"] != b["berth_id"]:
        return False
    a_start = _at(a["berthing_start_at"], "a.berthing_start_at")
    a_end = _at(a["berth_release_at"], "a.berth_release_at")
    b_start = _at(b["berthing_start_at"], "b.berthing_start_at")
    b_end = _at(b["berth_release_at"], "b.berth_release_at")
    if a_end <= a_start or b_end <= b_start:
        raise ValueError("Berth occupancy intervals must have positive duration")
    return max(a_start, b_start) < min(a_end, b_end)
