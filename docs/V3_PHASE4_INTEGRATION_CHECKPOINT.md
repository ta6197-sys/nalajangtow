# V3 Phase 4 — integration checkpoint

Base `fa8eb74` (PR #32). This is a development checkpoint, not the completed Phase 4 optimizer. It does not change the legacy engine or use PostgreSQL.

`validate_candidate(snapshot, rows, *, policy, as_of_at, initial_inventory=None, carried_transfers=None, tank_commitments=None, same_tank_policy=None, time_limit_seconds=30)` independently replays supplied physical berth rows and constructs exact fixed-rate transfer segments from their cargo work intervals. A fractional final second uses an exact reduced rate without changing the cargo quantity. It selects one source-connected and cargo-compatible tank through the existing V3 tank service and checks the signed external process flows. It extends the tank projection beyond 72 hours when selected work requires it. A protected tank commitment must match the independently constructed transfer definition.

`PHYSICAL_AND_TANK_ONLY` means only that the supplied physical rows passed the existing berth validator and their tank subproblem passed; it is never an approved or executable schedule. Existing physical replay uses berth-rate durations. Where a vessel rate would require a different duration or any other discrepancy exists, this checkpoint refuses the candidate; it does not yet propagate a revised duration or try alternate berth/start candidates. The caller must supply complete source flows and, for noninitial projections, valid initial inventory and carry. Individual supplied rows can be a subset of the scenario.

Pending mandatory work: candidate generation and iterative no-good feedback, tank-induced duration propagation, protected 24-hour freeze and complete call coverage, verified weather, mapping V3 contracts to the existing authoritative contract evaluator, settlement precision, true demurrage objective, valid bounds, A/B/C objectives, final independent integrated validation and shared time budget. The seven unresolved settlement precision cases and same-tank concurrency policy cannot be silently assumed. Do not interpret a solver status for this subproblem as integrated optimality.

Reproduce the isolated synthetic integration checks:

```
PYTHONPATH=/private/tmp/docktimize-phase4b-venv/lib/python3.11/site-packages:/opt/homebrew/lib/python3.11/site-packages .venv/bin/python -m unittest tests.test_v3_integration_checkpoint
```

## Finite feasibility search follow-up

`search_feasibility(snapshot, *, as_of_at, candidate_etbs, policy, commitments=(), initial_inventory=None, carried_transfers=None, tank_commitments=None, same_tank_policy=None, time_limit_seconds=30, max_iterations=20)` repeats berth CP-SAT proposals and the independent tank check. Only an `INFEASIBLE` result from finite tank assignment triggers a no-good, which excludes the exact complete set of physical rows. It does not forbid a berth/time appearing in another schedule. Timeout, invalid inputs, missing data and unresolved policy stop with an unresolved result, without excluding a candidate. The elapsed time budget is passed to each subsequent subsystem; Python preprocessing and deterministic validation can overrun it, so it is not yet a strict preemptive global deadline.

An isolated integration fixture used an initial 94 bbl, maximum 100 bbl and external inflow of 5 bbl/hour until 02:00. The initial 02:00 berth proposal overflowed before loading; excluding that complete selection allowed the 01:00 proposal to validate. Source data and quantities were not altered. Search is restricted to caller-provided ETBs and existing physical options. `PHYSICAL_AND_TANK_ONLY` remains an unapproved subsystem result; it is not a cost-optimized schedule. Objective, authoritative contract pricing, actual-duration propagation, full 24-hour freeze, complete scenario coverage and final integrated validation remain open.

## Source transfer rate and duration policy

The opt-in physical policy `cargo_duration_policy: VESSEL_BERTH_RATE_V1` uses the lower of the supplied vessel and berth MT/hour transfer rates, or the sole supplied rate. Missing or invalid rates fail explicitly. Cargo mass divided by the effective rate gives the exact duration; only the finish event is rounded up to the next integer second, and the tank bridge uses an exact reduced last-second rate to preserve cargo. Work completion, hose disconnection, switching and clearance are replayed from the derived duration before the independent berth/resource and tank checks. Existing policies without this key retain the prior `BERTH_RATE_V1` behavior and fingerprints.

Synthetic evidence: a 1 MT call at a 1 MT/hour vessel rate and 960 MT/hour berth rate requires 3,600 seconds, rather than the earlier 4 seconds. Its work completion and clearance move accordingly; an overlapping following call is independently rejected. This is source-rate propagation, not a policy for arbitrary tank-induced delays. The integrated search still requires future work for tank-constrained feasible slowdowns, candidate generation, contract costs and complete operational validation.
