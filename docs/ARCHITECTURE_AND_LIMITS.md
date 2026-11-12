# Docktimize V5 — full-scale validation layer

**Base**: `ta6197-sys/nalajangtow` `main` commit `a90990292ff34d93b340f36aa8445bda022e57df` (PR #64 Rolling and PR #65 optimality-reporting fix already merged).

This package adds a **bounded execution, diagnostics, checkpoint reader and local V5 preview** around the existing CP-SAT engine. It does not replace the optimizer, alter transfer quantities, add artificial cost weights, or assert global 58-day optimality.

## What is implemented

1. `v5_scale.py`: original-data validation, exact file loading, deterministic subset selection, 5/10/20/140 scope gates, the existing integrated V5 Rolling call and source-derived preview metadata.
2. `v5_run_review.py`: strict structural fingerprint, clock, state, provenance and selected-schedule/settlement consistency checks; JSON output reduction for browser safety. **Not** independent physical replay.
3. `v5_preview_api.py`: safe read-only listing and window access; opt-in, administrator-allowlisted **3/5-vessel** execution; bounded per-window solver calls and one active worker; no shell command or legacy DB changes.
4. CLI: preflight, gate runner, 6/12/24 candidate benchmark, structural audit, and the **original Stage-4 independent replay on a complete set of saved checkpoints**.
5. Web: actual 72-hour berth Gantt, independently replayed tank inventory from checkpoint, candidate cost history, finite-domain optimality, 24-hour carryover and validation. No fabricated sample success values.
6. Unit and JS tests plus fail-closed integration script. Existing V5 source modules are **not** overwritten.

## Important limits and unresolved policies

- V5 requires **140 synthetic cargo calls, 11 berths, 3 pooled tanks, 15 hard FIX ETBs, and 696 six-hour external-flow records**, validated against original workbook/JSON hashes. The source files are **not** in the public repo or this package. They must be provided by the operator.
- Current source has 58 days of six-hour external flow (696 = 3 tanks x 232 records). A 72-hour window late in the period can exceed input coverage. The solver must report unresolved coverage; do **not** invent future flows. An extension, if any, needs separately auditable provenance and explicit V5 integration; V3 synthetic extension support must not be assumed to carry over automatically.
- The 4-1/4-2 berth phase rule, low-GRT deep-draft exception, 02:00–08:00 pilotage restriction and aspects of Laycan To settlement remain unresolved. Fail closed. The full 140-call gate cannot be considered passed until selected calls have fully specified allowed operating policy.
- Web input and scenario registration are **admin-controlled**. Policy and departure pilotage windows must come from explicit, validated JSON files, not this package's defaults. Example configuration is a *skeleton*, not permission or validated input.
- The `CLEAR_WEATHER_BASELINE_V1` mode is a **synthetic projection**, not actual contractual settlement.
- The initial 5/10/20-call gates are **restricted-scope diagnostic scenarios**. Excluded ships are not included in their berth/stock interactions and those gates cannot establish full-port schedule feasibility.
- A `status == REQUESTED_WINDOWS_VALIDATED` does not mean that every selected call has completed. The full 140-call gate reports `all_scope_calls_completed` separately. A failure to complete all 140 is **not** success.
- CP-SAT `OPTIMAL` is for its generated finite candidate domain in a single 72-hour window. There is no proof of globally minimal 58-day cost or all continuous ETBs.
- **Never sum overlapping `window_demurrage_cents`**; only the original Rolling accounting state can establish uniquely accrued/completed cost when it reports complete coverage.

## Build/test scope

Unit tests in `tests/test_v5_full_*.py` and `tests/web-v5-preview.test.cjs` are intentionally source-independent and can run without OR-Tools. They verify logic around serialization, scheduling metadata, checkpoint access, API security and browser formatting. They are **not** proof of a real 140-vessel optimum.

For end-to-end correctness, Codex must run the existing Stage 1–4 tests **and** run the full source-verified gate with the operator's actual V5 bundle and explicit pilotage/policy data. A completed run must be independently replayed via `verify_resume.py` and report the exact solver/status and validation outputs.

## Scaling

| Gate | Selected calls | Rolling windows | Purpose |
|---|---:|---:|---|
| `S5` | 5 | 2 | Small real-input smoke test |
| `S10` | 10 | 3 | Complexity and carryover |
| `S20` | 20 | 3 | More berth/tank conflicts |
| `R7` | 20 | 7 | Week-long state persistence |
| `R14` | 20 | 14 | Two-week state persistence |
| `FULL140` | 140 | 60 | Full period only when data/policies cover it |

The FULL140 window count is an explicit **initial experiment** (60 rolling steps); increase only if the model supports longer flow coverage and chosen experimental scope. The number of calls in the 72-hour model at each step is determined from ETA/FIX and protected calls; it is not necessarily 140.

## Web scope

Legacy `/api/plan`, `/api/apply` and `/api/operate` continue to use their existing system. V5 has separate `/v5-preview` and `/api/v5-preview/...` routes. Preview execution never writes the legacy PostgreSQL planning tables. Docker binds `127.0.0.1:8765` for local-only access.
