# V3 Phase 2 — isolated physical berth subsystem

Branch `feat/11berth-physical-scheduling-v3`, base `e02e670` (Phase 1 merged through PR #28). No commit or push is made per the user's current Git preference. All code is isolated under `physical_scheduling`; the existing 8-berth planner, contract evaluator, tank model and PostgreSQL workflows are unchanged.

## Model and independent validation

`solve_berth_schedule(snapshot, *, as_of_at, candidate_etbs, policy, commitments=(), time_limit_seconds=30)` selects exactly one compatible berth/ETB option per in-scope call using CP-SAT. The berth set is scenario-defined; there is no 11-berth constant in reusable scheduling logic. Full normalized source inputs pass V3 Phase 1 preflight before search. Small isolated synthetic test snapshots exercise the subsystem without importing an operational database.

Ordinary ETBs are choices from explicitly provided candidate instants, not fixed baseline ETBs. A hard FIX has only its exact input ETB and an eligible berth choice. POB before ETA and physical work before the run boundary are rejected. Missing candidates or conflicting FIX return scoped failure; no hard timestamp is shifted. ETU is derived from selected phases. Full supplied commitments are preserved byte-for-value and independently replayed, including remaining physical occupation. No confirmation or database mutation is performed.

The 72-hour detailed start window and 24-hour freeze boundary are reported. Work beginning within 72 hours may complete beyond it. Calls outside the detailed horizon are returned separately; future FIX jobs with no explicit berth commitment are not independently assigned/protected in this subproblem and are explicitly pending. Freeze protection applies to passed commitments; there is no automatic operational commitment inferred from a previous baseline.

Every option is phase-replayed. Berth-rate cargo duration is `ceil(cargo_MT / berth_MT_per_hour * 3600)` seconds, a proposed lower bound awaiting tank validation, not an authoritative actual work duration. Cargo quantity is unchanged. Permitted work windows and supplied WORK/BOTH restrictions generate active transfer intervals and pauses; the berth remains occupied during pauses. No sunset stop is imposed when night work is enabled. External weather beyond supplied restrictions and tank availability are not yet evaluated. These remain mandatory integration checks, not waived constraints.

The synthetic berth resource is reserved from berthing-maneuver start through physical clearance. Same-cargo group occupation uses ETB to ETU as defined by the source group. Same-berth occupancy, switching clearance, source closure windows, 4142 resource conflicts and global tug demand are enforced as incompatible option pairs or cumulative constraints. The independent validator reconstructs all timestamps, then checks phase/resource collisions without relying on CP-SAT constraints. An invalid result is withheld. Objective is feasibility (constant zero), not demurrage or waiting minimization. Solver OPTIMAL does not establish all-time or integrated scheduling optimality.

Hose connection can start after ETB. Additional finite choices use switching-release instants from other candidate replays/commitments on relevant resources. This supports an alongside vessel waiting for shared equipment. The release expansion is bounded by the generated input domain (one expansion pass); it is not exhaustive over continuous time or arbitrary multi-vessel chains. No continuous-time infeasibility claim is made.

## Explicit user synthetic policy

`build_user_policy` consumes a daily sunrise/sunset CSV, hashes its exact bytes, and records an explicit synthetic policy identifier. Approved phase defaults are berthing 3,600 s, hose connection 7,200 s and unberthing 3,600 s; explicit per-berth duration overrides are supported. Source berth hose-disconnection and switching minutes are always preserved. POB, berthing start, ETB, hose connection start/end, cargo work intervals/start/end, hose disconnection, switching, departure POB, unberthing and ETU/clearance are separate events. The approved synthetic boarding-to-maneuver gap is zero, with the separate event timestamps retained.

GRT <30,000 may board at night only where individual berth permissions permit it. GRT 30,000–49,999 has Last POB at sunset minus 90 minutes, GRT >=50,000 at sunset minus 120 minutes. These apply to pilot boarding, not ETB; departure boarding uses the same rule as explicitly selected by the user. Daylight-constrained maneuvers must also finish within the supplied daylight window. Individual berthing/unberthing permissions may be supplied per berth and overridden per call for source-backed draft/other exceptions. No ballast/import status is inferred from LOAD/DISCHARGE. Missing permissions are pending data, not presumed approval.

Cargo work is allowed overnight, subject to independent input restrictions. Tug availability is four port-wide. GRT <30,000 uses one tug and GRT >=30,000 uses two for both arrival and departure. Exact tug occupation intervals are returned and tug capacity is released at maneuver completion.

4142 uses explicit berth IDs from the source group, independently managed berth-maneuver resources, and one conservative shared resource for hose connection, cargo, hose disconnection and switching. Switching begins after hose disconnection; the succeeding hose connection may wait until that shared resource is free. A succeeding cross-berth ETB must satisfy both `ETB_B >= ETU_A - 120 min` and the source `ETB_B >= WorkEnd_A`, and cargo/hose resources cannot overlap. The GRT 3,000 exception remains inactive. The source workbook's maneuver mutex and work-end-based switching text are retained as explicit source-rule overrides under the new user synthetic policy, not rewritten as official SK regulations.

## Actual V3 policy audit — not a successful 140-call solve

All 140 calls have at least one dimensional/cargo-compatible berth: 556 call/berth pairs. None of the 15 FIX timestamps is less than 60 minutes after ETA. This is only documentary compatibility/preflight, not schedule feasibility.

The actual policy audit returns `POLICY_PENDING` with 13 entries:

- Eleven berth-specific night-permission sets are not available as verified structured inputs. Historical facility text mentions import/ballast/draft conditions and does not map unambiguously to every V3 berth; it is not silently converted into permission.
- Two 4142 event-definition conflicts: the disputed legacy `WorkEnd + 120 min = ETU` is 7,200 seconds, while source hose disconnection 120 min plus user separate unberthing 60 min implies 10,800 seconds. The code compares these relations; it does not add both relations or double-count the hose duration. An unresolved definition blocks search/approval for that policy. Explicit phased resolution is used only in labeled unit fixtures, not enabled for the supplied 140-call source.

The generated report is `results/v3_physical/e05016325dc42d3780aee521/policy_validation_report.json`. A template under `config/v3_synthetic_phase_policy.template.json` intentionally contains unresolved fields and is not executable as an approved policy.

## Reproduction

Policy audit (exit 2 is expected while unresolved):

```
python -m src.daily_planning.physical_scheduling.cli \
  --snapshot results/v3_preflight/670af17c193ab3d738703352/normalized_snapshot.json \
  --build-user-policy --daylight-file src/daily_planning/daylight_windows.csv \
  --as-of 2026-11-03T00:00:00+09:00 --coverage-end 2026-11-11T00:00:00+09:00
```

`--night-permissions <json>` accepts `{berth_id: {berthing: bool, unberthing: bool, by_call: {call_id: {...}}}}`. `--berth-overrides <json>` supplies only the explicitly supported phase durations in seconds. Policy audit remains unresolved on the supplied 4142 ETU conflict. `--policy <json> --candidate-etbs <json> --as-of <time>` runs the isolated finite model only after all required policy inputs validate. The candidate file maps call IDs to lists of ETB instants; FIX entries in that file are not used to change the hard source time. `--output-file` creates a new file exclusively, never overwriting source or prior output.

Tests: 20 new tests passed, covering compatibility, FIX preservation and moving a non-FIX, berth conflicts, missing policy, pilotage rejection, commitments/input immutability, second precision, overnight pauses, restriction/FIX conflict, 4142 versions and alongside waiting, policy-definition conflicts, tug limits, GRT cutoff logic, approved 60/120/60 defaults/overrides, and beyond-72h completion. Full regression: 272 tests, 271 passed, one existing fresh-initial-state PostgreSQL integration test skipped. No legacy test was modified.

## Remaining acceptance limits

Phase 2 is not accepted for operational use or the complete 140-call scenario: berth-specific night/draft conditions and 4142 event resolution remain pending; external weather and authoritative tank-dependent durations are not integrated; unassigned future FIX beyond 72 hours still need the later rolling coordinator. The new status is only `BERTH_SUBSYSTEM_FEASIBLE` after independent validation under a supplied synthetic policy. No source workbook/JSON, operational state, settlement rule or legacy experiment was changed. Phase 3 time-series tank work and later coordinator/A/B/C objectives were not started.
