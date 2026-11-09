# Codex handoff — next implementation after unpacking the additive ZIP

Repository: `ta6197-sys/nalajangtow`. The ZIP was built against `main` commit
`1be95988c77bcb5205a0cda374c1cf765b14bf7b`.

## First actions

1. Fetch the current `main`. Check for any paths in this ZIP already existing.
2. Inspect `docs/V2_PHASE3_OPERATIONAL_CONSTRAINTS.md` and
   `docs/V2_PHASE2_INTEGRATION.md`; do **not** claim either source-backed
   operational restrictions or historical provenance are complete.
3. Run the new isolated tank tests and demo. Also run the existing repository
   regression suite, recording exact counts and skips.
4. Confirm these files are additive and do not modify `context.py`,
   `exact_search.py`, `optimization.py`, `operations.py`, `models.py`, or the
   existing Risk Engine.

## Implement next in a *separate* pull request

**Objective: finish source-backed Phase 3 where inputs exist; then design the
Phase 4 Pyomo/HiGHS allocation subproblem and its integration contracts.**

- Reuse `facility_master.py` and `horizon_policy.py`. Do not rebuild forecast
  or event candidate generation, which are already present on `main`.
- Track confirmed vs source-unknown berth restrictions. Refuse to infer new
  SK switching durations or tank connections where none are documented.
- Keep the current 72-hour CP-SAT optimizer / 24-hour commitment logic intact.
- Extend the tank package with an optional **separate** optimization backend:
  use deterministic inventory simulation when start/rate/assignment are fixed;
  use LP for fixed topology with variable rates; use MILP when tank/route
  assignment is discrete. Gate extra dependencies and use isolated fixtures.
- Make all continuous solver outputs pass independent mass/volume/time checks;
  do not equate MILP numerical tolerance to contractual 1kg conservation.
- Do not activate any new model as the production default.
- For the one tank per call policy, handle partial work on the same tank across
  multiple time segments; never use a second tank to hide capacity shortage.
- Carefully reconcile the 30-minute inventory grid with exact event boundaries
  when work begins/ends off-grid. A grid-only capacity check can miss a
  violation and is explicitly insufficient.
- Later, implement a versioned coordinator that does not simply discard an
  infeasible tank schedule without testing valid berth/time alternatives.
- Preserve user approval for any operational commit or change.

## Minimum tests for the next PR

- Existing A/B/C baseline costs and schedule signatures unchanged when new
  features are disabled.
- Same source snapshot and weather run deterministically.
- Tiny reference LP/MILP: 2 calls, 2 berths, 2 connected tanks, distinct
  capacities, variable transfer rate, fixed process net outflow.
- Cross-check LP/MILP candidate against this package's independent validator.
- One-call overflow at a sub-30-minute event is rejected.
- One-call mass conservation uses validated density; reject missing conversion.
- A call cannot split between two tanks.
- If any FIX/Freeze or source-backed physical constraint is violated, return
  a structured failure or operator-approval request; do not silently relax it.
- No writes to source workbooks or operational PostgreSQL while testing.

## Required closing report

Base commit, files changed, dependency additions, test results, numerical
feasibility/bound evidence, remaining input gaps, and whether each acceptance
criterion was met. Do not auto-merge or continue to the next phase.
