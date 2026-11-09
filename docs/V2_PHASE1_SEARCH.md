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
