# V5 exact inventory event preparation

`prepare_inventory_events` builds a tank-specific union of projection, external
flow and all supplied option transfer boundaries. It uses the same extracted
`build_v5_transfer` function as the independent V5 candidate validator. Each
option contains exact `Fraction` cargo, signed cumulative contributions, and
separate receipt and dispatch rates for each tank interval. External signed
flow contributions are separate and are not equipment throughput.

The caller supplies all relevant complete physical options, including the
unchanged full physical row for each carried or protected transfer. A carry
must match its full transfer fingerprint, boundary, tank, berth and remaining
quantity. Only cargo after the boundary contributes. Finished cargo is ignored
only when its physical row is explicitly protected. Missing or overlapping
external coverage and transfers extending beyond the projection raise errors.
The returned datetime and Fraction values are Python objects, not JSON values.

This prepares coefficients only: it does not select a candidate, check tank
bounds, add CP-SAT constraints, or establish schedule feasibility. The caller
must supply a validated starting inventory and use the independent validator.
Subsecond event durations are unsupported. Full source input and operational
policies remain the caller's responsibility.

```sh
/private/tmp/docktimize_v5_test_env/bin/python -m unittest tests.test_v5_inventory_events tests.test_v5_tank_inventory tests.test_v5_cost_aware tests.test_v5_physical_integration tests.test_v3_tank_inventory tests.test_v3_physical_scheduling
```
