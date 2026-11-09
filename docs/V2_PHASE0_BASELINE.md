# V2 Phase 0 baseline — preliminary synthetic measurements

Base: `a1c3591fc66c38a846724594e8509201839e4dab`. Instrumentation is isolated in `benchmarks/v2_phase0.py`; it does not alter the scheduler or connect to PostgreSQL. Run `.venv/bin/python benchmarks/v2_phase0.py --budget 2 --output /tmp/phase0.json`. Fixtures are generated synthetic inputs, not operational values. Times depend on hardware, OS scheduling, solver version and the wall-clock budget. The 2-second budget applies separately to each candidate; A shares its budget between its two objectives.

Workflow: `context.prepare_run` reads and hashes an input snapshot; `optimization.optimize_run` builds the finite domain, searches A/B/C, independently replays candidates, then saves results. `operations.confirm_candidate` replays and confirms, and `operations.advance_run` applies the 24-hour execution from `execution.execute_committed_window`. Forecasting and production DB mutation are outside this benchmark.

| Calls | Options including deferred | CP-SAT calls | Solver seconds | Replay calls | Replay seconds | Total seconds | A cost (cents) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2 | 26 | 8 | 0.0253 | 15 | 0.0035 | 0.0364 | 15063 |
| 4 | 52 | 74 | 0.2239 | 81 | 0.0397 | 0.2749 | 33355 |
| 8 | 104 | 256 | 1.7713 | 261 | 0.2188 | 2.0259 | 70710 |

Measured CP-SAT time dominates exact replay on these fixtures. Replay includes contract pricing, so pricing is not independently measured; domain generation is recorded separately in JSON. Snapshot preparation here measures synthetic fixture construction, not PostgreSQL snapshot reading. A/B/C elapsed time, exact costs, signatures, deferred count, solver status, bound and gap appear in the machine-readable output. The C fixture has no prior tentative plan and uses the existing wait fallback. Final database persistence and final independent replay are not included. This does not establish a production bottleneck.

The search master selects one generated berth/start option or deferred choice per call. Deferred choices have zero cost lower bound; a deterministic FCFS tail supplies exact completion costs during replay. Each examined choice is excluded from later master solves. The bound is valid for the generated finite domain and fixed replay-tail policy, subject to the existing model's assumptions; it does not cover ungenerated starts or globally optimal tail assignments. A's cost and waiting tie-break are separate searches; the secondary record's objective bound is a waiting bound, and its primary cost bound appears in `primary_cost_search`.

Regression: `PYTHONPATH=/opt/homebrew/lib/python3.11/site-packages .venv/bin/python -m unittest discover -s tests -t .` with local socket permission: 171 tests, 170 passed, 1 skipped (PostgreSQL integration opt-in). The existing historical A/B/C risk fixture replay tests pass, but no complete historical scheduler rerun against an isolated source PostgreSQL snapshot has been established. The initial unmodified virtualenv lacked openpyxl. Two new isolated fixture/instrumentation tests pass. No existing solver files were changed.

Acceptance: unchanged implementation verified by diff; historical scheduler equivalence and PostgreSQL workflow remain unverified; reproducible synthetic timing and separate solver/replay timings are available; synthetic bottleneck measured, production bottleneck unresolved. Phase 0 is therefore **not fully accepted**.

Phase 1 recommendations are provisional: profile representative frozen snapshots and separately measure pricing before prioritizing replay caching; compare CP-SAT invocation overhead and candidate generation under controlled budgets before changing search. Do not claim production speedups from these fixtures.
