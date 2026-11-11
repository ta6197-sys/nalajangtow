# V3 Phase 4 — unified, read-only validation reports

This tool collects existing Phase 4 validation into **one JSON and one Markdown audit**. It does not replace the berth, FIX, tank inventory, contractual settlement or independent validators; instead, it replays each saved incumbent using the existing independent validator. A saved "VALID" flag is not trusted.

## Single 72-hour result

Run from repository root with the **same normalized snapshot and policy** used by the optimization run:

~~~bash
python -m benchmarks.v3_phase4_validation_report \
  results/v3_crude_plus_search_cuts_evidence/d40227f4bd9026ffd33e47df/normalized_snapshot.json \
  results/v3_phase4/first_72h_independent_v2.json \
  --as-of-at 2026-11-03T00:00:00+09:00 \
  --pricing-policy CLEAR_WEATHER_BASELINE_V1 \
  --output-json results/v3_phase4/first_72h_audit.json \
  --output-md results/v3_phase4/first_72h_audit.md
~~~

For non-default optimization policy, provide --policy-json. Provide --same-tank-policy ALLOW or FORBID **only when that exact policy was used in the original run**. For a non-initial rolling window, pass the source-authenticated commitments, completed rows, carried inventory, carried transfers, and tank commitments in their respective JSON files. If these state inputs are unavailable, independent replay must not claim an operational approval.

## Rolling 72-hour windows advancing every 24 hours

~~~bash
python -m benchmarks.v3_phase4_validation_report \
  SNAPSHOT.json ROLLING_RESULT.json \
  --mode rolling --expected-days 58 \
  --output-json results/v3_phase4/rolling_audit.json \
  --output-md results/v3_phase4/rolling_audit.md
~~~

Each verified day's synthetic carryover is recomputed, the following window's completed/protected-call declarations and carried inventory are compared, and the final recomputed state is checked against the stored final state. Incomplete daily coverage, lack of a validated incumbent or missing final carry state cannot be reported as PASS.

## Interpretation

* PASS: fresh synthetic input and independent V3 incumbent replay succeeded for the requested scope. It is **not** operational approval and does **not** prove continuous-time/global optimality.
* INCOMPLETE: no valid incumbent or requested windows not verifiably covered.
* FAIL: input, source availability, replay, or carryover mismatch.
* NOT_RUN (subchecks): observed weather/SOF, actual operating-DB commitment authority and continuous-time optimality cannot be inferred from synthetic results.

Sources are never modified; existing audit filenames are not overwritten. Gross demurrage improvements and solver-reported runtimes are included when present. Only independently validated incumbents count as successful outputs.

Run focused tests with: python -m unittest tests.test_v3_validation_runner -v
