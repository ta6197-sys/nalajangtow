# Risk Settlement Precision and Coverage V3

## Current validation and activation state

The original workbook `input/source_contracts.xlsx` contains 144 contracts, 1,639 SOF events and 144 Golden labels. The original minute calculator and `SECOND_PRECISION_SIMULATION_V1` match all 144 Golden settlement records, field by field, **when using the workbook's supplied effective laytime start**. Compared fields include allowed, used, excluded and unused time, demurrage duration and onset, demurrage cents, despatch cents, and net cents. No Golden label is missing. The read-only audit is in `docs/risk_settlement_golden_audit_v3.json`.

A separate source consistency check finds **40 contracts** where the workbook's effective start precedes the result of its NOR, notice and `early_start_if_work` fields. Recomputing those contracts from the derived start produces 133 field-level differences from Golden, including 21 net-charge differences. Each contract ID, field, expected value, actual value and suspected cause is in the audit JSON. The original contract engine is unchanged. The versioned configuration currently sets `clock_start_divergence_policy: BLOCK`, so the PostgreSQL-backed second-precision policy **cannot run** until the source data is corrected and reaudited. `LEGACY_CONTRACT_PARITY` remains the default.

The supplied V2 100-scenario output was audited read-only against its local snapshot and weather source. All 68 unresolved scenarios per candidate had a sub-minute **completion** boundary passed as the contract finish. No additional failure was caused solely by berth release, work interval or night pause precision. Across those 68 scenarios, DEM-0103 appeared 36 times, DEM-0112 32, DEM-0011 9 and DEM-0047 4; some scenarios involve multiple calls. The original minute calculator rejects that finish boundary. The four calls in each A/B/C candidate occupy four different berths, so a previous call on the same berth cannot delay another in that run. V3 additionally identifies a propagated delay only when the actual start is later than a counterfactual start without the preceding berth release.

## Policies

- `LEGACY_CONTRACT_PARITY` uses the original minute-based calculator. Sub-minute contractual boundaries remain `UNRESOLVED`; physical schedules remain available. This is the default and preserves `NO_SHOCK_BASELINE` exactly.
- `SECOND_PRECISION_SIMULATION_V1` is an explicit, simulation-only Risk policy. It uses a versioned JSON configuration and requires a matching explicit CLI option. It is **not a verified contract clause or legally verified charge**. Physical arrival, work, completion and berth-release times remain integer-second timestamps and are never rounded for settlement.

The second policy determines effective laytime start from each contract's NOR, notice and early-start rules. Allowed minutes are multiplied by 60 exactly. Each SOF/Risk event is processed with the contract's SHINC/SHEX day rule, holiday list, weather COUNT/EXCLUDE/HALF credit, owner/terminal exception, post-demurrage exception, daily rate and despatch policy. Decimal seconds track fractional weather credit and excluded time. For a new sub-minute exhaustion timestamp, assumption `EFFECTIVE_START_ANCHORED_60_SECOND_EXHAUSTION_V1` starts demurrage at the next 60-second boundary anchored to that contract's effective laytime start. The anchor never resets at a new event. Zero allowed laytime starts demurrage at effective start. USD cents use `ROUND_HALF_UP` once, at the original final charge calculation. Minute-aligned cases delegate to the original settlement calculator for exact field parity.

Floor, ceiling and nearest-minute projection are limited to isolated sensitivity tests. They are not available as Risk settlement policies.

## Isolated tests and audit

```sh
python -m unittest discover -s tests -t . -p 'test_risk_*.py' -q
python -m src.daily_planning.risk.golden_validation --workbook input/source_contracts.xlsx --out results/risk-v3-golden-audit.json
```

The audit output reports every mismatch as contract ID, field, expected, actual and suspected cause. It separately reports source effective-start divergences. After the user updates the workbook, its SHA-256 and expected divergence count in `config/risk_settlement_second_v1.json` must be updated only after inspecting the new audit. Do not edit source contracts or Golden labels merely to satisfy the test.

## User-run PostgreSQL validation after source correction

The existing PostgreSQL service must be running. These one-off commands do not run the web service's migration startup command and omit `--persist`. They intentionally fail while the current clock-start gate remains blocked.

```sh
docker compose -p solver-daily build web
docker compose -p solver-daily run --rm --no-deps -T web python -m src.daily_planning.risk.cli --run-id f080131a-33d7-5ca8-8ac6-091930421063 --config config/risk_synthetic_multiblock_v1.json --evaluate --sample-count 100 --settlement-policy SECOND_PRECISION_SIMULATION_V1 --settlement-config config/risk_settlement_second_v1.json
docker compose -p solver-daily run --rm --no-deps -T web python -m src.daily_planning.risk.cli --run-id f080131a-33d7-5ca8-8ac6-091930421063 --config config/risk_synthetic_multiblock_v1.json --evaluate --sample-count 1000 --settlement-policy SECOND_PRECISION_SIMULATION_V1 --settlement-config config/risk_settlement_second_v1.json
```

The output includes policy, precision, assumptions, Golden audit, settlement coverage, exact unresolved boundaries, failure breakdown and disruption measures. Full-distribution VaR/CVaR is available only when every A/B/C scenario settles successfully; otherwise the report keeps conditional statistics separate.
