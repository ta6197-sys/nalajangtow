# V2 Phase 1 search — preliminary comparison

Base `171947f`; legacy `HEURISTIC_V1` remains default. `EVENTS_V1` adds verified available/physical release/previous plan anchors without removing legacy options and uses a bounded replay cache. `warm_start=True` additionally reserves a replay-validated heuristic choice for the first master solve if it satisfies the master cost cap and strict incumbent bound; hint use is reported. Once selected, a previously evaluated hint is excluded before continuing. Proof scope remains the generated finite domain plus deterministic tail, not all possible times. There is no inferred FIX, pilotage, switching, weather release or tank rule.

Run `.venv/bin/python benchmarks/v2_phase1_compare.py --budget 1 --output /tmp/phase1.json`. Synthetic fixture inputs only; 1 s budget per candidate, A shares it between cost and tie-break. One execution, not a statistically representative performance study. The events and events+hint variants both use caching, so they are not an isolated measurement of event generation alone.

| Synthetic calls | Options (each method) | Legacy total A/B/C seconds | Events seconds | Events + hint seconds | A/B/C cost cents (each method) |
|---:|---:|---:|---:|---:|---:|
| 2 | 26 | 0.019 | 0.024 | 0.025 | 15063 |
| 4 | 52 | 0.276 | 0.316 | 0.312 | 33355 |
| 8 | 104 | 1.021 | 1.023 | 1.022 | 70710 |

The 8-call A result was FEASIBLE rather than proved optimal and the hint was actually used; the other recorded searches were OPTIMAL in the restricted domain. All three variants had three distinct candidate signatures per fixture. No speedup can be inferred. The fixture contains no fixed physical-phase releases, so it does not demonstrate event-domain expansion. Search records and bounds are in `benchmarks/v2_phase1_results.json`.

Tests: full pre-final test suite 175 tests, 174 passed, one opt-in PostgreSQL test skipped; five event tests subsequently passed. The historical stored A/B/C schedules for two runs were independently replayed and reoptimized before this phase, matching costs and vessel details. These do not demonstrate historical results under the new event variant.

Remaining: first feasible latency, separate solver/replay timing, measured comparison of event-only against event+cache, broader event fixtures and validated hint edge cases, independent PostgreSQL integration for the new feature, and tests with original historical scenarios. Unsupported event families need verified operational data. Phase 1 is not yet accepted; do not infer operational readiness or speed improvement.
