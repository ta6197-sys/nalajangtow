# V3 Phase 4: resumable 58-day synthetic rolling run

\`benchmarks.v3_phase4_run_resumable\` launches consecutive 72-hour windows at 24-hour steps and saves each independently validated window. It does not change source data, FIX ETBs or production PostgreSQL state.

## Run

~~~bash
python -m benchmarks.v3_phase4_run_resumable \
  /path/to/normalized_snapshot.json \
  --output-dir results/v3_phase4/resumable_v1 \
  --days 58 --seconds-per-strategy 60 \
  --same-tank-policy ALLOW --max-new-days 2
~~~

Use \`--same-tank-policy ALLOW\` **only for an explicitly chosen synthetic scenario**, not as a claim about real piping/operations. \`FORBID\` represents the contrary policy; omitting the option keeps undecided overlaps unresolved. Existing concurrent external-process flow is treated separately. Never silently change hard FIX ETBs.

Resume with the **same** snapshot, physical policy and strategy flags:

~~~bash
python -m benchmarks.v3_phase4_run_resumable \
  /path/to/normalized_snapshot.json \
  --output-dir results/v3_phase4/resumable_v1 \
  --days 58 --seconds-per-strategy 60 \
  --same-tank-policy ALLOW --resume
~~~

Use \`--policy-json\` for a non-default original physical policy. Changes to source snapshot, policy, optimization flags, daily budget or strategy configuration invalidate resume. \`--max-new-days\` is a safe pacing control and can change between runs.

## Files and interpretation

* \`manifest.json\`: source, policy and run configuration fingerprints.
* \`day_NNN.json\`: independently verified incumbent and next 24h carry-state fingerprint; saved atomically.
* \`blocked_day_NNN.json\`: attempted strategies and the first unresolved day.
* \`summary.json\`: number of validated 24h transitions, distinct covered call IDs, next day, blocking information. This is **not** a double-counted 72h cost total.

The engine never advances from a failed day. Resume replays stored candidates and verifies their carried inventory. \`STOPPED_UNRESOLVED\` does not imply that the entire schedule is infeasible. A \`REQUESTED_WINDOWS_VALIDATED\` status refers only to the explicitly configured synthetic window experiment and all-calls coverage, **not** real operational authority or continuous-time global optimality.

The 2027-01-02 flow extension is synthetic and has a recorded \`known_at\` timestamp. Do not use it in plans before its information-availability time; do not fill any later gap. The original 2026-12-31 aggregate crude balance margin is only 455 bbl; a further 48h of fixed flows yields a negative net 96,570 bbl of crude. If all source cargo movements complete by that period, extra supply or a scenario correction may be necessary; the runner is forbidden to invent it.

## Tests

~~~bash
python -m unittest tests.test_v3_resumable_rolling -v
python -m unittest tests.test_v3_validation_runner -v
python -m unittest discover -s tests -v
~~~

The new focused test module covers checkpoint creation, resume without rerunning completed days, source-fingerprint mismatch, tampered carry-state rejection and a safe stop without an incumbent. Full native CP-SAT/HiGHS performance and all 58 real V3 windows still require independent execution in the project's solver environment.
