# Docktimize V5 — Stage 1 / S20 horizon pilotage-gap classification

Base: PR #86, `8a397e7aa43fed98bd8c297732694febf86407ff`.

## What the module proves

For an ordinary, unstarted, non-FIX, unprotected call, derive the same eligible berths and inbound pilotage windows used by V5. For every compatible berth, calculate the earliest permitted POB after `max(ETA, as_of_at, window.start_at)`. A candidate ETB requires the full berthing maneuver to fit in one inbound window, optional `latest_start_at` compliance, and `ETB < as_of_at + 72 h`.

If **no** compatible berth has any such candidate, the call cannot be berthed in the current 72-hour horizon under its arrival and pilotage requirements alone. This is a proof of **current-horizon pilotage gap**, not a proof of global physical infeasibility or of eventual completion.

A certificate is NOT issued when a FIX ETB exists, a call was already scheduled or protected, any compatible berth lacks policy evidence, the GRT/draft rule is unresolved, or at least one pilotage-compatible time remains.

## Why this matters for S20

The uploaded BALANCED source SHA256 `4d587107a051e83bcc3b9e80e74cbd17585e2d2b555eac1dc5fcb5faae2a7850` records:

- DEM-0030 ETA 2026-11-07 18:55 KST; GRT 42,200
- DEM-0084 ETA 2026-11-07 18:15 KST; GRT 42,200

The checked-in daylight source sets sunset 2026-11-07 at 17:22 KST. S20 window 3 starts 2026-11-05 00:00 and its 72-hour ETB domain ends **exclusively** 2026-11-08 00:00. For GRT 42,200 the verified pilotage derivation imposes daylight limits. This strongly indicates a legitimate horizon gap for these calls. The real-input diagnostic CLI must confirm **all** eligible berths before production code acts on this possibility.

## Execution mechanics

- Add the opt-in `horizon_pilotage_policy_version=V5_HORIZON_PILOTAGE_GAP_V1` in an entirely new config. Requires existing `V5_ROLLING_SAFE_ADMISSION_V1` and preserves all older runs when omitted.
- At each window: preserve the `due_call_ids`, exclude only calls with independently derived arrival/pilotage gap certificates, schedule the others using unchanged Stage 2B full-first and bounded-safe admission, and classify excluded calls as `DEFERRED` with `carryover_count`/waiting warnings.
- When **every** due call has a proven gap, allow a verified no-work (zero schedule) 24-hour transition using the existing inventory replay path; this does NOT price the deferred contract liabilities at zero.
- Record both the due/admitted/deferred partition and per-call certificates. Resume independently re-derives certificates from verified inputs and rejects tampered proof data even if the checkpoint fingerprint is recomputed.
- The CP-SAT objective (`MIN_TOTAL_DEMURRAGE`), inventory physical constraints, FIX ETB, settlement and 4-1/4-2 are untouched. These deferrals are based on a necessary physical prerequisite, not a relaxation or an unproved CP-SAT claim.

## Non-goals

Not a generalized "all zero candidates -> defer" rule. Not a guarantee that the next 24-hour window will solve or that 154 vessels can complete. Not a source-data edit. No DB migrations, API/UI changes, Simulation Bridge or shocks in Stage 1.

## Evidence gates

1. File/regression preflight, pure unit tests and repository integration tests.
2. S20 original window 3 evidence read-only diagnostic with both named call IDs.
3. New opt-in S5 / S10 baseline regression and S20 through the third window. Every checkpoint must pass independent replay and preserve source/policy fingerprints.
4. Confirm S20 is `REQUESTED_WINDOWS_VALIDATED`, NOT necessarily `all_scope_calls_completed`. Check deferral partitions; no missing vessels; no fixed/protected call deferred; no deferred liability priced at zero.
5. If any later window still fails due to another restriction or candidate-generation defect, report it and do not merge as a completed S20 fix.
6. Only then broaden to R7 / R14 / FULL154 in the next phase.
