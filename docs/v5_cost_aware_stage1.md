# V5 option-level CP-SAT cost objective

`schedule_v5(..., objective_mode='MIN_TOTAL_DEMURRAGE', ...)` prices each
complete, physically validated option using `price_v5_schedule`, then minimizes
the integer-cent sum on the existing selection variables. The default remains
`FEASIBILITY`. Fixed physical commitments are priced once as constants; all
options, including release-based hose alternatives, must be priceable. An
unresolved option stops the search rather than receiving a guessed cost.

`CLEAR_WEATHER_BASELINE_V1` is supported only by the existing settlement gate
for explicitly synthetic data. It represents a projected no-interruption
scenario, not an actual settlement or operational approval. Explicit evidence
and clock-audit rules continue to apply. The model does not optimize inventory:
the selected physical candidate is independently checked by the existing exact
inventory validator, and inventory failure is reported without replacing it.

`OPTIMAL` applies only to the generated finite physical option domain, including
any restrictions, exclusions, and protected commitments supplied to the run.
It is not a continuous-time or inventory-constrained optimality proof. A
`FEASIBLE` incumbent is explicitly labelled unproven. For an optimal integer
objective the bound is reported as the exact selected integer total; for a
nonoptimal incumbent CP-SAT supplies its floating-point bound.

The synthetic benchmark builds two compatible berths and two or three calls,
with two ETBs per call and different source contract rates. Its oracle enumerates
complete physical-option combinations, independently validates each schedule,
and invokes the settlement engine again; it does not use objective coefficients
or the solver's selected schedule as its answer. Both methods use the same
candidate domain and physical rules. The older method evaluates at most five
inventory-valid candidates, so its best evaluated value is not an optimality
proof. Timing includes interpreter warmup and should not be generalized.

```sh
PYTHONPATH=. /private/tmp/docktimize_v5_test_env/bin/python scripts/v5_cost_aware_benchmark.py
/private/tmp/docktimize_v5_test_env/bin/python -m unittest tests.test_v5_cost_aware
/private/tmp/docktimize_v5_test_env/bin/python -m unittest discover -s tests -t .
```
