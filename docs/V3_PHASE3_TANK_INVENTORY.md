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
