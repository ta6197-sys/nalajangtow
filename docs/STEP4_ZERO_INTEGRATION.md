# Stage 4 — explicit zero-minute berth phase integration

## Approved baseline (2026-11-04)

- Berthing and unberthing both have **0 minutes**.
- No separate measured berthing/unberthing records are available.
- Retain existing statuses; keep `berth_release_at` separate from cargo
  completion for modelling purposes.
- Preserve 140 WORK calls, 144 contracts, original SOF minute-level workload,
  30-minute decision grid, 72-hour planning, 24-hour freeze, and strict
  independent V3 preflight (33 incompatible synthetic calls remain blocked).

## Opt-in implementation

The environment variable `BERTH_PHASE_EXPERIMENT_KEY` must match a **new,
isolated** experiment key for the zero-phase policy to apply. It is forbidden
for `source-daywork-v2`. The policy is frozen in the input snapshot and
does not modify existing historical runs.

Four boundaries are provided in experimental evaluated vessel results:

1. `berthing_start_at` (equal to `work_start_at`)
2. `cargo_work_start_at` (equal to `work_start_at`)
3. `cargo_work_end_at` (equal to `completion_at`)
4. `berth_release_at` (equal to `completion_at`)

The solver's `NoOverlap`, evaluation overlap, FCFS berth-free timestamps,
protected commitment checks, and 24-hour completion checks now call the
shared physical occupancy boundary logic. The legacy row layout remains
unchanged when the new policy is not enabled.

**Important:** Engine times may be *naive KST* (without a UTC offset); the
boundary adapter normalizes a temporary copy for validation, retains the
original cargo timestamps, and emits explicit `+09:00` phase boundaries.

The existing `schedule_assignment.start_at/completion_at` columns continue
to mean cargo-work start/completion. Under this policy, these happen to equal
berth-occupation start/release. Any future nonzero times are **rejected**
rather than silently written to the legacy assignment schema. The persisted
commit lock covers the full occupancy boundary. No DB migration and no
new operation status are introduced.

## Verification

Run from the repository root in the same Python environment used for earlier
87-test regression:

```bash
./.venv-1/bin/python -m unittest discover -s tests -p 'test_berth_phase_zero_integration.py' -v
./.venv-1/bin/python -m unittest discover -s tests -v
```

Test that the older `test_source_postgres` does **not** count as passed if
skipped. A separate non-production PostgreSQL experiment with an isolated
experiment key is needed to prove candidate storage, confirmation, 24h
progression, and cost equivalence.

The independent V3 input currently contains 33 calls without a compatible
single berth; strict preflight must still fail. This change is not a
workaround for those 33. Do not claim full V3 optimization success based on
this zero-phase unit test or run a new physically gated experiment by
suppressing the preflight.

The time phases are **synthetic simulation definitions**, not observed SK
CLX maneuver durations or contracts. The SOF last event is not a verified
unberthing completion. This policy must not reinterpret contract laytime.
