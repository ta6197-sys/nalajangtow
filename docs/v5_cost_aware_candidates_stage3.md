# V5 cost-aware candidate expansion

Set `candidate_improvement_mode='COST_AWARE_V1'` on `schedule_v5`, together
with automatic adaptive candidates, `MIN_TOTAL_DEMURRAGE`, and
`EXACT_EVENT_BALANCE_V1`. The default `NONE` preserves the existing workflow.
Frozen domains cannot be combined with this mode. At most two improvement
rounds and 24 candidates per call/berth pair are supported.

Once a baseline solution has passed independent physical, inventory and
settlement validation, calls are ordered by their exact selected demurrage.
All non-fixed and non-protected calls remain eligible, including blockers.
For each eligible berth, proposals use the incumbent ETB ±30 minutes and
other selected ships' physical-clearance time plus the berth's maneuver time.
`CandidatePool.add` independently replays and validates every proposal, applies
existing policy checks, and records reasons and rejections. The domain only
grows. No external flow or unsupported contractual boundary is assumed to be
a useful ETB.

Every round shares the caller's time budget; the baseline solve and earlier
rounds reserve a fraction for later rounds. A newly solved candidate replaces
the incumbent only after all independent validations pass and its exact cost
is lower. A later failure keeps the previous validated incumbent. Its original
domain fingerprint is distinct from the final candidate-domain fingerprint;
an unsolved expanded domain is never labelled optimal. Solver optimality is
always limited to the modeled finite physical and inventory candidate domain.

The test fixture's 3-call initial domain had 12 candidates and an optimal
33,900-cent cost; after two rounds it had 21 candidates and 26,400 cents.
An independent exhaustive enumeration replays physical, inventory and
contract validation over the final options. These figures are synthetic
projected costs, not real settlement or operational approval.

```sh
/private/tmp/docktimize_v5_test_env/bin/python -m unittest tests.test_v5_cost_aware_candidates
```
