# V5 integrated finite-domain inventory model

`schedule_v5` accepts `inventory_constraint_mode='EXACT_EVENT_BALANCE_V1'`
only alongside `objective_mode='MIN_TOTAL_DEMURRAGE'`. The existing final
physical options and protected rows are passed in order to Stage 2A event
preparation. For every tank event, initial inventory plus fixed external flow,
protected contribution and selected-option contributions is bounded by the
source tank limits. Separate gross receipt and dispatch constraints are added
for each event interval; external flow is not vessel equipment throughput.

Each linear constraint is scaled with an exact least common denominator and
reduced by its integer greatest common divisor. Unsupported signed 64-bit
coefficients, denominators or total expression magnitudes fail closed. The
existing cost objective remains integer contract cents. Independent physical,
inventory and settlement validation runs after solving. A disagreement does
not qualify as integrated optimality.

The common projection covers all modeled transfer ends, or uses the caller's
explicit end and rejects any transfer extending beyond it. Source flow coverage
must cover the projection. Protected and carried records are checked by Stage
2A and protected profiles are fixed terms, never optional Boolean variables.

The independent synthetic oracle enumerates generated physical options, checks
physical and V5 inventory replay, then invokes the contract settlement engine.
Proof is limited to the supported generated finite domain and does not establish
real operational approval or continuous-time global optimality. Broader edge
cases, especially all carried/commitment combinations and solver timeouts,
need additional integrated tests before this stage can be considered complete.

```sh
/private/tmp/docktimize_v5_test_env/bin/python -m unittest tests.test_v5_inventory_constraints tests.test_v5_integrated_inventory tests.test_v5_inventory_events tests.test_v5_cost_aware
```
