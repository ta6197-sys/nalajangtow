# V3 Phase 3 — tank inventory (development checkpoint)

This is an isolated synthetic development checkpoint, **not an operationally approved schedule or a completed integrated model**. Original source data and PostgreSQL are not changed. V1 tank code remains independent.

## API

`validate_v3_inventory(snapshot, transfers, *, start_at, end_at, initial_inventory=None, same_tank_policy=None)` exactly replays fixed operations. Each transfer specifies `call_id`, `berth_id`, `tank_id`, `start_at`, `end_at` and, for a FIX call, `etb_at`. Source cargo, conversion, connection and direction are checked. Rates are the lesser of supplied `transfer_rate_mt_h` (call) and `max_transfer_rate_mt_h` (berth), converted using source `physical.bbl_per_mt`; tank rate fields are not used. The current implementation requires a constant full effective rate over the whole integer-second interval and requires exact cargo equality; non-integral-second final transfers and paused/multi-segment operations are not yet supported and are rejected rather than rounded.

`solve_v3_allocation(snapshot, transfers, *, start_at, end_at, initial_inventory=None, same_tank_policy=None, time_limit_seconds=30)` uses deterministic replay for fixed tank choices. Otherwise `eligible_tank_ids` supplies a finite choice set. A Pyomo/HiGHS binary model selects one tank per call; exact replay checks each proposal and verified invalid complete assignments are excluded. It is a finite enumeration approach, not an inventory-constrained MILP or a claim of global integrated optimality. Inventory failures can make it slow; timeout returns no incumbent. Same-tank simultaneous use is pending unless policy is explicitly `ALLOW` or `FORBID`.

Result version `V3_TANK_INVENTORY_V1` includes `status`, `issues`, snapshot and policy fingerprints, assignments and exact bbl/hour rates and quantities, per-tank event time series, extrema, signed external flow totals and 24-hour carried inventory. Numerical quantities are rational strings; timestamps have integer seconds and KST offset. `operational_approval` is always false. Flow signs are retained as source signed net inflow; linear inventory is checked at all vessel starts/ends, flow changes, projection endpoints and 24h boundary. Min/max at these points establish bounds between points for constant rates. No source values are modified.

For noninitial projection starts, exact inventory for every tank must be supplied. An operation crossing the start boundary cannot yet be represented as remaining cargo with its original assignment; callers must not assume in-progress operations are carried. Intervals crossing 72h can be included only when projection end and flow coverage extend through completion. This is not yet a rolling coordinator. Known FIX ETB is checked when supplied, but immutable commitments and berth, weather and settlement checks are pending independently.

## Physical scheduling

V2 synthetic policy `build_unbounded_synthetic_policy` keeps the old V1 policy available. Omitted tug capacity does not generate a cumulative constraint; omitted pilotage windows do not generate a GRT rule. POB coincides with start of 3600-second berthing; hose connection is 7200 seconds and unberthing 3600 seconds. Hose disconnection and switching use the berth source. Replay accepts explicit `unberthing_start_at` after hose disconnection and computes ETU from that start. Source 4142 overlap and shared resource rules remain independently checked; the old work-end-to-ETU relation is reported but phased synthetic resolution is explicitly selected. Waiting is replayable but finite candidate generation does not yet search unberthing wait instants.

## Evidence and outstanding work

The local supplied V3 snapshot, projected for its first 24 hours with **no vessel transfers**, produced 30 tank series, 30 carried inventories and zero violations. This confirms only fixed-flow data compatibility, not feasibility of the 140-vessel schedule. The source 4142 policy, tank concurrency, exact settlement precision and external weather remain independent concerns. Test evidence and current regression totals are reported in the session response.

Outstanding acceptance work includes broader synthetic coverage, exact partial-second-rate/partial-operation representation, complete committed/in-progress carryover, structured error classification, actual source-backed vessel transfer trials, and validation of finite MILP enumeration at scale. Phase 3 acceptance has not yet been demonstrated.

Local dependency limitation: importing HiGHS before OR-Tools in one test process produced a macOS dynamic-library symbol conflict between the installed `highspy` and OR-Tools libraries. The full unittest discovery order passed but does not establish that all import orders are safe. Resolve compatible native solver dependencies or process isolation before combining the subsystems.

## Carryover and process-isolation follow-up

The follow-up permits a transfer to cross a projection boundary and accepts exact piecewise-constant `segments`, including pauses between segments and an independently specified final rate. Each segment must use integer-second boundaries, a positive rate no greater than the applicable source-based effective rate, and the exact sum of segment quantities must equal the full source cargo. No physical event is rounded to a reporting interval. Thirty-minute reporting points have been added alongside the event boundaries.

`validate_v3_inventory` and `solve_v3_allocation` accept `carried_transfers`. At the 24-hour boundary, the result records the remaining bbl, call/berth/tank and a fingerprint of the original transfer. A subsequent projection requires that carry record and matching full transfer definition along with all tank inventories; mismatches are rejected. This validates the stated input against the previous projection, but is not a persistent or transactional rolling execution service and does not itself verify independently stored operational commitments.

For undecided tanks, HiGHS now runs in a separate Python process, so loading it does not load its native library into the OR-Tools process. Running both previously conflicting test groups in the former failing order passed. This addresses that reproduced local collision, not an exhaustive certification of every dependency combination. The finite allocation enumeration and its performance limitations remain.

A source-backed partial trial used DEM-0112, BERTH-04, TANK-03 and 81,360 bbl, at an illustrative time derived from ETA. The applicable maximum was 8,814 bbl/hour and total transfer time 33,231 seconds with an exact reduced final-second rate. The 96-hour projection returned tank inventory-limit violations, as expected for this trial; it does not prove that this vessel or the full 140-call scenario is unschedulable. No source input was altered.

Test evidence before the final reporting-grid addition: 286 tests run, 285 passed, one skipped. After adding the grid, the 14 focused V3 tests passed. Coverage remains insufficient to declare full Phase 3 acceptance: actual complete scenario, exhaustive commitment carryover, solver status behavior under timeouts and independent validation of all required constraints remain open. The earlier paragraphs describing carryover and partial segments as unsupported are superseded by this section.

## Protected commitments, budget handling and source trials

`validate_v3_inventory(..., commitments=...)` and `solve_v3_allocation(..., commitments=...)` accept an explicit list of complete transfer definitions relevant to the projection. Each must appear unchanged in `transfers`; missing, shifted, reassigned or otherwise altered definitions return `COMMITMENT_CHANGED`. The service does not independently retrieve or authenticate a commitment list. `commitments=None` is explicitly `NOT_PROVIDED` and remains pending, while an explicitly supplied empty list records that the caller supplied no relevant commitments. FIX ETB is still independently checked against the normalized source. Supplying commitments is not an operational confirmation and does not validate berth phases or weather.

A positive, finite time budget is required. A process timeout returns `TIME_LIMIT_NO_INCUMBENT`; this check was tested using an injected timeout, not by claiming a particular machine's runtime. The isolated assignment solver does not yet establish whether a timed-out solver had an unloadable incumbent, so no unvalidated proposal is returned.

Three repeatable, independent, illustrative source-backed trials can be run using:

```bash
PYTHONPATH=/opt/homebrew/lib/python3.11/site-packages .venv/bin/python -m benchmarks.v3_tank_source_trial results/v3_preflight/670af17c193ab3d738703352/normalized_snapshot.json --count 3
```

On this dataset the three test calls DEM-0112, DEM-0011 and DEM-0047 all returned inventory-limit violations under their illustrative single-vessel allocations. These are neither executable schedules nor a proof of infeasibility for different allocations or the full 140-call scenario. The script uses normalized source inputs and never writes to them.

Follow-up budget verification found that the prior 10-second process allowance could exceed the requested wall-clock limit. The subprocess now has the requested timeout itself, including startup. In a local 1-microsecond budget trial, it returned `TIME_LIMIT_NO_INCUMBENT`; process startup, solver construction and independent validation are all within that external budget. A deterministic fixed-assignment replay uses no solver process and does not interpret this value as a projection timeout.
