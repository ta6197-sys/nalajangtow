# Docktimize V5 — Stage 3A: provenance-bound risk impact screening

## Baseline

- GitHub main at PR #88: `515227754dbee5a0e09f5f697296a23e64f8c403`.
- Requires existing `v5_simulation_bridge_v2.py` and validated V5-native NO_SHOCK replay.
- Scenario: `V5_BALANCED_154_EXT_20270109` (154 calls, 11 berths, 3 tanks, 816 external-flow records).
- Does not modify any original source, policy, solver objective, checkpoint or settlement calculation.

## Scope

This is **impact screening**, not a full risk simulation or a recovered schedule.

Supported shock events:

1. `ETA_DELAY`: move the hypothetical arrival by an integer number of seconds. For each saved row, compare new arrival to the saved POB. Report fixed-row arrival slack and breach; **do not move FIX ETB** or silently reschedule. Due/deferral membership may need to be recalculated even if the saved row remains reachable.
2. `WORK_DURATION_MULTIPLIER`: multiply the exact sum of saved cargo-work seconds using rational arithmetic, round required seconds *up* to the next integer. This is a **minimum additional cargo-work duration estimate**, not an updated event schedule: breaks, work windows, transfer rates, and tank balance remain to be replayed.
3. `WEATHER_CARGO_SHUTDOWN`: an explicit berth-level interval in which **cargo handling** is assumed stopped. Report exact overlap in seconds with existing cargo-work intervals. Weather-driven restrictions on inbound pilotage or port closure require another explicit model, and are not inferred.

If an affected call is due but not scheduled (for instance a deferred S20 call), it remains in the output as a call needing admission reevaluation. The engine does not manufacture an ETB or a cost for it.

## Required safety gate

The CLI loads and verifies the *real* source/profile, policy, daylight, complete checkpoint chain and runs PR #88's `verify_run_no_shock` against the same source and saved run. This cannot be replaced by an untrusted JSON assertion. If the baseline parity status is not `NATIVE_NO_SHOCK_BASELINE_PARITY_PASS`, the shock assessment exits with code 2 and writes no success output.

## Output

A fresh local output directory contains:

- `native_no_shock_evidence.json`: live V5-native baseline replay evidence.
- `risk_impact_v1.json`: source and run fingerprints, event-by-event fixed-schedule conflict indicators, affected calls and windows, baseline vessel statuses, waiting and deferral context, and per-window cost references.

When the scenario contains shocks, output status is **`SHOCK_IMPACT_SCREEN_ONLY_REPLAY_NOT_RUN`**, not "simulation successful". A revised shocked schedule, new tank event series, and shocked demurrage are `NOT_RUN` or `null`, **never zero**. The baseline window cost references are overlapping and must not be summed.

No scenario writes into the existing run/checkpoint or source directory. S20 window validation does **not** assert all 20 calls completed, and `OPTIMAL` is finite-candidate-domain optimality only.

## Example command

```bash
python scripts/v5_risk/run_risk_impact_v1.py \
  --config config/v5_balanced_ext_20270109.s20_pilotage_gap.example.json \
  --run-directory /actual/path/to/results/v5-922dc358416a408b \
  --scenario-file examples/v5_risk_three_shocks.example.json \
  --output-directory results/v5_risk_impact/study_001
```

`--run-directory` is the **existing, saved run directory** (the example is only a naming illustration); locate the actual S20 result in the user's local environment. Always use a new `--output-directory`.

## Next, Stage 3B

Build a **real, independent perturbation/recovery solver** that recomputes shifted ETBs/work intervals, pilotage and berth resources, exact inventory events, contract clocks/settlement, 24-hour state carryover and post-shock costs. Apply scenario-specific freeze/FIX rules, verify no-shock parity as the hard regression baseline, compare NO_RECOVERY vs RECOVERY explicitly, and test S5/S10/S20 then R7/R14/FULL154. None of those claims are made by Stage 3A.
