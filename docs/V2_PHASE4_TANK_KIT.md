# Docktimize V2 — additive tank feasibility kit

**Verified GitHub base (2026-11-09 13:24 KST):** `1be95988c77bcb5205a0cda374c1cf765b14bf7b` (PR #24 merged).

## Why these files exist

The repository already contains the Phase 0/1 benchmark and event-search experiments,
Phase 2 read-only 168-hour forecast, and the initial Phase 3 source-traced facility
master. `docs/V2_PHASE3_OPERATIONAL_CONSTRAINTS.md` explicitly states Phase 3 is
**incomplete**: real switching, pilotage, FIX, cross-berth overlap, and tank
connections were not yet implemented. Do not bypass that work.

This kit consists only of **new files** for a read-only, separately testable
**Phase 4 tank model foundation**. It must not be presented as the complete
Phase 4 Pyomo/HiGHS optimizer. There is no automatic connection to the
CP-SAT model, PostgreSQL, or the 168-hour forecast.

## File contents

- `src/daily_planning/tank/network.py`: strict 30-tank, 10-million-bbl input master validation; cargo and connection references; exact rational quantities.
- `src/daily_planning/tank/validator.py`: per-call single-tank assignment, transfer rates, cargo volume and optional kg/density cross-checks, transfer overlap and connection validation.
- `src/daily_planning/tank/inventory.py`: exact event-aware inventory projection with fixed external process net flows; 30-minute reporting and second-precision physical events.
- `schemas/tank_network_v1.schema.json` and `tank_schedule_v1.schema.json`: portable data shapes, not independently sufficient to validate physical feasibility.
- `tests/fixtures/tank_v1/`: explicitly synthetic 30-tank example plus two transfer calls on **fictional** berths.
- `tests/test_v2_tank_inventory.py`: self-contained unit tests without database, Pyomo, or weather data.
- `scripts/check_tank_fixture_v1.py`: stand-alone demonstration.
- `docs/V2_PHASE4_CODEX_HANDOFF.md`: follow-up development instructions for Codex.

## Physical and contractual scope

- Exactly 30 physical tanks with total installed capacity 10,000,000 bbl in the V2 input profile. **The fixture distribution is invented solely for isolated tests**; it must never be inserted into production as real SK infrastructure.
- Per-tank single cargo group. A call must pick **one tank**, but separate calls could share the same tank at different times.
- No tank-to-tank transfer. No splitting one call across multiple tanks. No automatic vessel relocation after partial unloading.
- `DISCHARGE` increases stock, `LOAD` decreases stock. The process net flow, a signed fixed input, is **not** a decision variable. For crude oil, a fixed outflow of 1,000 bbl/h is encoded as `"-1000"`.
- Quantity values are exact decimal **strings** in barrel units. Source shipment quantities are presently in kg; a reliable per-cargo density or measured-volume source is required before conversion. Optional `cargo_mass_kg` must exactly equal `cargo_volume_bbl × density_kg_per_bbl`. Missing physical density must not be guessed.
- Transfer time is parsed as a timezone-aware integer-second timestamp. Every start/end boundary is evaluated; 30-minute points are for display and rolling projections, **not** a constraint on physical ETB/work/hose timing.
- A shared tank or berth can transfer cargo for only one call at a time in this conservative test model. Other pump/pipe sharing rules are pending verified engineering data.
- The model tests the supplied transfer plan **as is**. It does not schedule ETB, change a transfer rate, move a vessel, optimize inventory, or calculate contractual demurrage.
- It **does not** yet model nonzero switching, pilotage, SK#4-1/SK#4-2 special overlap, weather, FIX, or 24-hour Freeze; those belong to the operational scheduler. No result grants operational approval.
- Sources are identified explicitly as `SYNTHETIC_FIXTURE` or `SOURCE_VERIFIED`. An asserted source reference for the latter is required, but external truth still must be audited before operational use.

## Inventory balance

For tank k over exact event interval [t,t+dt]:

`stock[k,t+dt] = stock[k,t] + dt_hours × (fixed_process_net_flow[k] + Σ signed_transfer_rate[call,k,t])`

All arithmetic is `fractions.Fraction` until rendering. Output includes an approximate decimal `bbl` and an **exact** rational `exact_bbl`. Verify all physical and business unit conventions before external deployment. No quantity is silently rounded to a minute, 30-minute bucket, or integer barrel.

## Validation and usage

From the repository root **after extraction**:

```bash
python -m unittest discover -s tests -p 'test_v2_tank_inventory.py' -v
python scripts/check_tank_fixture_v1.py
```

Expected fixture: 30 tanks, 49 reporting time points/tank over 24h, mass verified for 2 synthetic calls; TANK-01 final 506,000 bbl and TANK-04 final 180,000 bbl; `FEASIBLE` with `operational_approval: false`.

The 168h test verifies 337 reporting points per tank and a call crossing the 72h planning boundary.

## Limits and next steps

1. **Phase 3 unfinished**: complete source-backed switching, pilotage, FIX, and cross-berth rules independently. No synthetic exceptions.
2. **Phase 4 data pending**: populate source-backed tank capacities, connections, group compatibility, actual/projected inventory, transfer limits, fixed outflow. 30 / 10m bbl is only an aggregate design requirement; it is not a source for allocating individual capacities.
3. **Phase 4 LP/MILP pending**: develop a separate Pyomo/HiGHS allocation model if rate allocation, tank choice, or shared pipelines must be optimized. A fixed schedule is handled by this deterministic validator, not by MILP.
4. **Phase 5 integration pending**: feed physical infeasibilities back to CP-SAT; no-good exclusions must be logically valid, since time or duration changes can restore feasibility.
5. **Phase 6+ pending**: persist actual vs projected inventories, 24h commit/carryover, risk shock replay and operator approval.

No existing file is modified by this kit. It is safe to review and commit on a separate branch, not to deploy as a replacement optimizer.
