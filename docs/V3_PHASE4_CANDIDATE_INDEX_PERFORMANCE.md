# Phase 4 candidate preprocessing acceleration (V1)

The Phase 4 search repeatedly constructs a finite CP-SAT berth model before testing HiGHS tank assignments. This patch optimizes two CPU-bound preprocessing steps without deleting ETB options or changing hard constraints:

1. \`release_index.py\`: index berth and shared 4-1/4-2 switching-release events once, reusing them for each initial candidate instead of scanning every other candidate. Preserve identical timestamps, same-call exclusion, \`LOCKED_ONLY_V1\` / \`ALL_OPTIONS_V1\` and deterministic chronological ordering.
2. \`conflict_index.py\`: use an expiry heap and interacting-berth buckets to enumerate only candidates with overlapping physical envelopes and a potentially shared berth/dock/group resource. The original \`pair_conflicts()\` and \`validate_result()\` still decide the actual constraint; neither is weakened.

### Verification

\`python -m unittest tests.test_v3_fast_candidate_indices tests.test_v3_conflict_index tests.test_v3_physical_scheduling -v\`

\`python -m benchmarks.v3_candidate_index_microbenchmark --options 1300\`

The focused synthetic index-equivalence tests (five cases) passed in an isolated Python environment with a simplified import stub. A 1,300-option microbenchmark there returned the same 7,411 potential pairs and took approximately 0.259s for a full index scan versus 0.058s for indexed lookup. These numbers **are not** a CP-SAT/HiGHS end-to-end speedup claim. Existing integration tests and full 140-vessel day-3 runs require the project's native Solver environment.

### Next local verification

1. Run the repository tests above and \`python -m unittest discover -s tests -v\`.
2. Compare baseline and patched code on the **same** V3 day-3 snapshot, carried state, 72h window, \`same_tank_policy=ALLOW\`, exact flow, same CPU and time budget; repeat the experiment to characterize variance.
3. Record generated candidate counts, physical-option counts, pair checks, preprocessing duration, MILP solver time, first independently valid incumbent and gross demurrage. Use the existing unified validation runner and resumable rolling executor.
4. Do not count a \`TIME_LIMIT_NO_INCUMBENT\` as proof of infeasibility. Do not label a best-enumerated cost as global optimal.
5. If the day-3 bottleneck persists, profile tank-subprocess startup, cached exact tank assignments and model rebuild; make separately reviewable changes. Avoid sharing OR-Tools and HiGHS native runtimes in one process without a validated isolation strategy.

This patch does **not** modify FIX ETB, 24h Freeze, vessel quantities, tank limits, weather/contract simulation policy or source data.
