# V2 Phase 1 search performance

Base `0253682`; legacy `HEURISTIC_V1` is the default. `EVENTS_V1` adds verified availability, physical berth release and previous-plan anchors without removing legacy choices. Its optional bounded replay cache is scoped to snapshot and weather content. `warm_start=True` reserves a replay-validated heuristic choice only when the master cost cap and current objective bound admit it. A previously evaluated hint is excluded when returned by CP-SAT. Optimality applies only to the finite generated choices and the existing deterministic replay tail; it does not cover ungenerated start times.

## Synthetic comparison

Run `.venv/bin/python benchmarks/v2_phase1_compare.py --budget 1 --output /tmp/phase1.json`. Synthetic fixtures, one measurement each, one second per A/B/C search; A shares its budget between primary cost and waiting tie-break. A final independent validator replay checks every returned candidate. Times vary with hardware and solver scheduling. The benchmark observes cache retrievals and exact replays for first-feasible latency; CP-SAT and replay time are separately measured and exclude final validation. Because the small fixtures have no phase-extended confirmed work, legacy and event candidate sets are identical here.

| Calls | Variant | Options | Total A/B/C s | CP-SAT s | Replay s | First feasible A/B/C s | Cost A/B/C cents |
|---:|---|---:|---:|---:|---:|---|---|
| 2 | legacy | 26 | .043 | .034 | .004 | .003/.003/.005 | 15063/15063/15063 |
| 2 | events only | 26 | .017 | .012 | .003 | .001/.003/.005 | 15063/15063/15063 |
| 2 | events + cache | 26 | .024 | .012 | .002 | .003/.004/.006 | 15063/15063/15063 |
| 2 | events + cache + hint | 26 | .026 | .013 | .002 | .003/.004/.006 | 15063/15063/15063 |
| 4 | legacy | 52 | .267 | .223 | .037 | .001/.005/.007 | 33355/33355/33355 |
| 4 | events only | 52 | .266 | .220 | .039 | .001/.005/.008 | 33355/33355/33355 |
| 4 | events + cache | 52 | .271 | .213 | .034 | .003/.006/.008 | 33355/33355/33355 |
| 4 | events + cache + hint | 52 | .275 | .217 | .034 | .003/.006/.009 | 33355/33355/33355 |
| 8 | legacy | 104 | 1.022 | .876 | .127 | .002/.008/.012 | 70710/70710/70710 |
| 8 | events only | 104 | 1.019 | .876 | .126 | .002/.008/.012 | 70710/70710/70710 |
| 8 | events + cache | 104 | 1.020 | .854 | .120 | .004/.008/.012 | 70710/70710/70710 |
| 8 | events + cache + hint | 104 | 1.022 | .850 | .123 | .004/.008/.012 | 70710/70710/70710 |

Eight-call A was FEASIBLE at timeout; all other recorded candidates were OPTIMAL within the restricted domain. A hint was used for eight-call A. All runs produced three distinct candidate signatures. Bounds, gaps, candidate signatures, solver calls and replay invocation counts are recorded in `benchmarks/v2_phase1_results.json`. A secondary search record does not include all primary CP-SAT calls, so the separate instrumentation provides cumulative timing and counts. The single-run evidence does not establish a speedup, and the 8-call cases exhausted their A budgets.

## PostgreSQL and tests

`benchmarks/v2_phase1_postgres_check.py` only accepts `POSTGRES_DB=docktimize_v2_phase0_isolated` and refuses a `DATABASE_URL` override. Using a read-only transaction on the copy of the supplied test DB, the four-call historical snapshot produced 148 options. A was FEASIBLE with signature `1ac30af970c69c15899286480937e5da865c79a366b85d31fbcdd395a9ea21a6`; B and C were OPTIMAL within the generated domain, with signatures `ecfe1a2e2503b6259ba8937c91cba513c9de770f8a1c66528f96dce31326391a` and `3dacc41ae816212cdbd3bdb3647f629ee6c943d5d35208dcead970ed6ae50864`. Each cost 6,736,250 cents and passed independent exact replay. This covers read-only search, not persistence or new-feature confirmation. The existing isolated transactional confirmation/advance check also passed and rolled back.

Full regression: 178 tests run, 177 passed, one skipped because the original PostgreSQL test requires a fresh source DB. Additional hint tests check cost-cap rejection and excluded signatures. Historical stored A/B/C schedules from two runs were reoptimized under legacy search and matched costs and vessel details; the four-call historical snapshot was also searched using the new feature for A/B/C, without asserting all new-feature signatures match historical alternatives.

## Remaining acceptance gaps

Verified operational rules for pilotage, switching, FIX, contract boundaries and tank events are not available to the generator; they must not be invented. Existing weather simulation adjusts invalid starts but explicit weather-release event coverage has not been established. Phase-extended physical release is tested as an anchor in isolation, but no integrated fixture demonstrates expanded candidate quality. The PostgreSQL event test exercises A/B/C search and replay, not `optimize_run` persistence. One run per small synthetic fixture is insufficient for performance conclusions; repeat representative cases under controlled budgets. Phase 1 remains incomplete and should not be presented as operationally ready.

## Final Phase 1 conclusion — versioned weather release comparison

The original source workbook identifies its source as SYNTHETIC; the following is a saved project snapshot, not a claim about live port operations. This snapshot has four calls and five weather blocked-to-workable transitions within the 72-hour window. `EVENTS_WEATHER_V2` adds those transition instants as optional anchors under the existing `RULE`; it retains all legacy options, does not force operations onto a grid, and is opt-in. The existing `EVENTS_V1` and legacy configurations remain unchanged. Unsupported pilotage, FIX, switching, tank and unverified contractual-boundary events remain pending inputs.

Four variants were run three times each at 2-second and 10-second A-search budgets on the same snapshot with rotated execution order. Reproduce using `.venv/bin/python benchmarks/v2_phase1_repeated.py --snapshot results/source-daywork-v2/f080131a-33d7-5ca8-8ac6-091930421063/input_snapshot.json --weather input/weather_for_optimizer_30min.csv --output /tmp/phase1-repeated.json --budgets 2 10 --repeats 3`. The snapshot and source weather file are local inputs and are not included in this repository. `benchmarks/v2_phase1_repeated_results.json` contains all 24 measured runs and solver traces. Every returned schedule passed independent replay. Times below are medians; candidates are counted including deferred options.

| Budget | Variant | Options | Domain s | Solver s | Replay s | Solver calls | First feasible s | Cost cents |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 2 s | legacy | 148 | .0201 | 1.5670 | .4165 | 200 | .0039 | 6736250 |
| 2 s | weather events only | 196 | .0267 | 1.6415 | .3404 | 165 | .0042 | 6736250 |
| 2 s | events + cache | 196 | .0267 | 1.6064 | .3369 | 163 | .0163 | 6736250 |
| 2 s | events + cache + hint | 196 | .0266 | 1.5982 | .3432 | 164 | .0160 | 6736250 |
| 10 s | legacy | 148 | .0203 | 8.4373 | 1.4877 | 682 | .0039 | 6736250 |
| 10 s | weather events only | 196 | .0268 | 8.6705 | 1.2556 | 565 | .0043 | 6736250 |
| 10 s | events + cache | 196 | .0274 | 8.5690 | 1.2412 | 565 | .0169 | 6736250 |
| 10 s | events + cache + hint | 196 | .0272 | 8.5544 | 1.2507 | 565 | .0164 | 6736250 |

All 24 searches returned the same A schedule signature and 6,736,250-cent cost; all were FEASIBLE at timeout with valid cost lower bound 0 and reported relative gap 1.0. A hint was used in all six hint runs. Weather anchors increased the finite domain by 48 choices (32.4%) without improving cost in this case. At 10 seconds, median solver calls fell from 682 to 565 (17.2%) for events alone. This is consistent with a larger master taking longer per call; wall-clock budgets were exhausted, so it is not proof of a general performance regression. Caching and hints did not change the incumbent in this experiment. No global optimality or production improvement is established. The observed zero lower bound and repeated master solving motivate investigation of a stronger mathematically proved deferred-call bound; no bound was strengthened in Phase 1 without such a proof.

`benchmarks/v2_phase1_persistence_check.py` uses only the isolated PostgreSQL copy and an outer rollback. A new weather-event run persisted three candidates, recorded `EVENTS_WEATHER_V2` and the hint policy, accepted an identical idempotent retry, rejected a retry with a different hint setting, and left no planning-run row after rollback. Existing 24-hour confirmation/advance was tested separately on the copy and rolled back; no operational schedule was confirmed by the new test.

Regression: 179 tests, 178 passed and one initial-state PostgreSQL integration test skipped. Weather tests verify a blocked-to-workable anchor is added, expands the generated domain, and preserves legacy candidates. All changes are versioned and disabled by default.

**Acceptance assessment:** independent validation of the measured returned schedules passes; source contract evaluator and default legacy path remain unchanged; baseline candidates remain in the event domain; time, options, calls, bounds and gaps are measured; no claim covers ungenerated starts. Evidence is limited to the listed snapshots and synthetic fixtures. Given no demonstrated gain and a larger domain, do not enable `EVENTS_WEATHER_V2` or warm starts by default. Phase 1's measured conclusion is that the tested event and hint changes did not improve cost or usable runtime on the supplied scenario; a stronger proved bound and representative future scenarios are necessary before revisiting that recommendation. Proceed to Phase 2 only on explicit authorization, preserving the feature gate and the legacy baseline.
