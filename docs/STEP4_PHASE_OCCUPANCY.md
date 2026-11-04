# Stage 4 — berth/cargo/unberthing phase contract

## Scope of this draft

Stage 4 begins with a **pure phase-interval validator**, not a rewrite of the
operating schedule or contractual laytime engine.

The existing scheduler uses `work_start_at` as cargo-start and
`completion_at` as cargo-complete. Its CP-SAT `NoOverlap`, replay,
evaluation tail, berth utilization, freeze commitment and operational carryover
all currently use this cargo-work interval. Simply adding an unberthing column
to a report would be incorrect, because the next vessel could be scheduled
while the berth is still occupied.

New explicit timeline fields:

1. `berthing_start_at`: beginning of physical berth occupancy
2. `cargo_work_start_at`: beginning of cargo work
3. `cargo_work_end_at`: end of cargo work
4. `berth_release_at`: completion of unberthing; next vessel may use berth

Single-berth occupancy: `[berthing_start_at, berth_release_at)`.
Cargo working duration **excludes** berthing and unberthing; remaining WORK
and kg are not decremented during either maneuver.

`berthing_minutes` and `unberthing_minutes` are **explicit SYNTHETIC
scenario inputs** unless facility-specific documentary/observed data is later
provided. No default nonzero SK operating duration is assumed. Inputs must
be nonnegative integer multiples of 30 minutes. A zero-duration legacy
timeline preserves historical occupancy exactly.

## Not yet implemented

- CP-SAT `NoOverlap` using full occupied intervals for candidate and
  protected vessel choices
- Source/frozen inputs and scenario versioning for durations
- Heuristic seed generation and FCFS deferred evaluation by eligible berth,
  with berth-release rather than cargo-complete availability
- Full candidate replay, confirmation and `schedule_commit.locked_until_at`
  using berth release; 24h carryover when cargo work ends before unberthing
- Berthing/unberthing weather, tide, tug/pilot/daylight feasibility
- Repricing laytime at distinct phase boundaries. Do **not** change the
  existing contract clock without an explicit contractual rule.

## Acceptance tests for the next integration PR

- Zero durations reproduce unchanged v2/v3 schedule, demurrage, status and
  source contracts
- Nonzero unberthing prevents next call from using that berth prematurely
- Freeze and carried states remain consistent when unberthing crosses 24h
- Work minutes and conserved kg are unchanged by maneuver durations
- Explicit invalid input/unknown facility never silently receives a duration
- All outcomes remain separate from the 33 blocked independent SYNTHETIC V3
  calls; this draft does not alter their profiles or bypass strict preflight

**This draft's tests validate only the pure phase contract. It is not yet a
passing end-to-end Stage-4 scheduling feature.**
