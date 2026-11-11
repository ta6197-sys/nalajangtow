# V5 inventory-aware reoptimization

`schedule_v5` validates each independently checked physical schedule using the
existing exact V5 tank replay. For inventory bounds and gross receipt/dispatch
violations only, it excludes the **whole selected schedule** using the existing
`forbidden_schedules` no-good and asks the same CP-SAT candidate domain for an
alternative. Individual assignments are not removed and no approximate
inventory constraints are added. Candidate generation and input validation are
not rerun between inventory retries.

`max_inventory_reoptimizations` defaults to three retries after the initial
schedule (at most four physical schedules evaluated), as confirmed by the user.
A single `time_limit_seconds` budget covers input verification, candidate
generation, physical solving, inventory replay and retries in both manual and
automatic modes. Noninterruptible library calls can finish after the budget, but
such a run cannot be returned as a combined valid result.

`reoptimization_attempts` retains each physical schedule, independent physical
status, exact inventory result, violation codes and related tank/call/time data.
`inventory_violations` aggregates the failures; `forbidden_schedule_count`,
`solver_attempt_count` and `inventory_reoptimization_solver_calls` provide
search counts. `inventory_constraints_added` remains empty: only exact
whole-schedule exclusions are used. A duplicate returned schedule stops the
search instead of being evaluated again. Non-retryable input, flow, carryover or
transfer-definition errors stop immediately rather than being treated as a
scheduling problem.

`CANDIDATE_DOMAIN_EXHAUSTED` means only the supported finite candidate domain
was exhausted. If operationally unresolved candidates were omitted, the stop
reason additionally marks policy pending. Timeouts and retry limits are distinct.
No inventory-invalid schedule can produce `PHYSICAL_AND_INVENTORY_VALID`; that
result does not constitute contract, global schedule or operational approval.

`scripts/v5_inventory_reoptimization/run_example.py` executes an isolated
synthetic two-schedule example with actual CP-SAT and source daylight, preserving
a fixed ETB. It is explicitly labelled synthetic; real V5 input regressions are
run separately. No changes are made to the original data.
