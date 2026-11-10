# V3 Phase 4 — integration checkpoint

Base `fa8eb74` (PR #32). This is a development checkpoint, not the completed Phase 4 optimizer. It does not change the legacy engine or use PostgreSQL.

`validate_candidate(snapshot, rows, *, policy, as_of_at, initial_inventory=None, carried_transfers=None, tank_commitments=None, same_tank_policy=None, time_limit_seconds=30)` independently replays supplied physical berth rows and constructs exact fixed-rate transfer segments from their cargo work intervals. A fractional final second uses an exact reduced rate without changing the cargo quantity. It selects one source-connected and cargo-compatible tank through the existing V3 tank service and checks the signed external process flows. It extends the tank projection beyond 72 hours when selected work requires it. A protected tank commitment must match the independently constructed transfer definition.

`PHYSICAL_AND_TANK_ONLY` means only that the supplied physical rows passed the existing berth validator and their tank subproblem passed; it is never an approved or executable schedule. Existing physical replay uses berth-rate durations. Where a vessel rate would require a different duration or any other discrepancy exists, this checkpoint refuses the candidate; it does not yet propagate a revised duration or try alternate berth/start candidates. The caller must supply complete source flows and, for noninitial projections, valid initial inventory and carry. Individual supplied rows can be a subset of the scenario.

Pending mandatory work: candidate generation and iterative no-good feedback, tank-induced duration propagation, protected 24-hour freeze and complete call coverage, verified weather, mapping V3 contracts to the existing authoritative contract evaluator, settlement precision, true demurrage objective, valid bounds, A/B/C objectives, final independent integrated validation and shared time budget. The seven unresolved settlement precision cases and same-tank concurrency policy cannot be silently assumed. Do not interpret a solver status for this subproblem as integrated optimality.

Reproduce the isolated synthetic integration checks:

```
PYTHONPATH=/private/tmp/docktimize-phase4b-venv/lib/python3.11/site-packages:/opt/homebrew/lib/python3.11/site-packages .venv/bin/python -m unittest tests.test_v3_integration_checkpoint
```
