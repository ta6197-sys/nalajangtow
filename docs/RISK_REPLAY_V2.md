# Shock-aware Risk replay V2

`NO_RECOVERY` keeps the stored candidate's berth, order, planned times and committed decisions. Physical replay starts at the snapshot's `as_of_at`, preserves completed work and the completed prefix of ongoing work, and applies arrival, remaining-work and weather shocks afterward. Earlier arrivals do not advance a planned start. A delayed actual start or changed completion is recorded as a commitment deviation when the call is frozen; it is not a physical constraint failure.

Each replay result separates `physical_status` (`FEASIBLE`/`INFEASIBLE`), `commitment_status` (`ON_PLAN`/`DISRUPTED`), and `settlement_status` (`SUCCEEDED`/`UNRESOLVED`). `actual_schedules` contains planned and realized start, completion and berth release, second-precision work and pause intervals, kg balances at as-of/24h/72h, delay seconds, predecessor and cause codes. Physical occupancy uses half-open intervals and the existing SK eligibility checks. The original candidate and snapshot are read only.

The original minute-based contract calculator remains the pricing authority. A physical result with a sub-minute settlement boundary is retained but marked `UNRESOLVED_SUBMINUTE_SETTLEMENT_RULE`; it has no fabricated cost. Full-distribution cost risk is available only if all A/B/C settlements succeed. Otherwise conditional cost measures identify their settled-only population. The CLI also prints coverage, failure breakdown and commitment/delay measures.

## Local isolated validation

```sh
python -m unittest discover -s tests -t . -p 'test_risk_*.py' -q
```

This includes stored A/B/C zero-shock parity and a deterministic 100-scenario fixture replay. It does not access PostgreSQL.

## User-run PostgreSQL replay

Use an existing A/B/C planning run UUID. The database service must already be running. These commands build the feature branch's web image and start one-off read-only Risk processes without the web service's migration startup command. There is deliberately no `--persist` option.

```sh
export RISK_RUN_ID='replace-with-existing-run-uuid'
docker compose -p solver-daily build web
docker compose -p solver-daily run --rm --no-deps -T web python -m src.daily_planning.risk.cli --run-id "$RISK_RUN_ID" --config config/risk_synthetic_v1.json --evaluate --sample-count 100
docker compose -p solver-daily run --rm --no-deps -T web python -m src.daily_planning.risk.cli --run-id "$RISK_RUN_ID" --config config/risk_synthetic_v1.json --evaluate --sample-count 1000
```

For each run, inspect `coverage_by_candidate`, `failure_breakdown_by_candidate`, `disruption_by_candidate`, `full_distribution_available`, `risk_by_candidate`, `conditional_risk_by_candidate`, and individual `failures`. A full run with unresolved or infeasible outcomes intentionally has no full-distribution VaR/CVaR.

`LIMITED_RECOVERY` is an interface in `extensions.py` only. Its future optimizer must fix completed work and immutable commitments, carry forward realized state, and reoptimize eligible future decisions through the existing CP-SAT, berth, pricing and rolling-horizon rules after NO_RECOVERY validation.
