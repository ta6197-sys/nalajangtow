# Docktimize — PR 68: V5 Demurrage Optimizer Evidence Audit (No Database Changes)

You are a senior Python, OR-Tools CP-SAT, maritime berth scheduling, tank-inventory, contractual demurrage, and automated-testing engineer.

## 0. Goal and authoritative baseline

Repository: `https://github.com/ta6197-sys/nalajangtow`.

Last observed `main`: `d42204de3e94c4d4d592cff68aeea31d662079ba` (2026-11-12 00:39 UTC; PR #67 merged). **Re-check the actual remote `main` before touching any file.** Preserve newer compatible code if the branch has advanced. Suggested branch: `feat/v5-optimizer-evidence-audit`.

This is an **audit-first task**, not a redesign. Determine whether the already implemented `INTEGRATED_COST_AWARE_V1` pipeline can produce a properly validated, contract-cent-costed berth schedule for a small, real-source V5 scope. Distinguish a valid schedule, a proved minimum over the **generated finite candidate domain**, and global optimum over all ETBs and the whole 140-call/58-day period.

**DO NOT edit any database, schema, migration, seed, PostgreSQL connection, Docker service, legacy data, or UI/API code. Do not launch DB migrations or modify production/source V5 files.** Do not implement A/B/C, ML, risk, a new objective, or full-scale benchmarking in this PR.

## 1. Package scope — additive only

Apply only the provided new files, preserving their relative repository paths:

- `src/daily_planning/integrated_v3/v5_optimizer_evidence.py`
- `scripts/v5_audit/audit_optimizer.py`
- `tests/test_v5_optimizer_evidence.py`
- `docs/V5_OPTIMIZER_AUDIT_KO.md` (operator reference)
- `README_KO.md` and this instruction document, for local use

These additions call existing modules; they do **not** copy or replace the physical scheduler, inventory engine, settlement engine, rolling coordinator, or checkpoint validators. If the target files already exist in a newer branch, inspect differences and avoid blind overwrite.

Important existing entrypoints:

- `src/daily_planning/integrated_v3/v5_physical.py` — `schedule_v5`
- `src/daily_planning/integrated_v3/v5_rolling_optimizer.py` — `optimize_integrated_window`
- `src/daily_planning/integrated_v3/v5_rolling.py` — `run_v5_rolling`
- `src/daily_planning/integrated_v3/v5_settlement.py` — `price_v5_schedule`
- `src/daily_planning/integrated_v3/v5_scale.py` — source preflight and gated execution
- `src/daily_planning/integrated_v3/v5_run_review.py` — checkpoint structural validation
- `src/daily_planning/integrated_v3/v5_rolling_audit.py` — read-only chain audit
- `scripts/v5_full/verify_resume.py` — source-verified physical, inventory, settlement and carryover replay

## 2. Required verification order

**A — Check repository state.** Record `git status --short`, `git branch --show-current`, `git rev-parse HEAD`, and actual remote `main`. Confirm all Stage 1–4 modules above exist. Do not discard uncommitted changes. Do not commit, push, or create a PR without a new explicit request.

**B — Run additive unit tests first.** From the repository root in a Python 3.12 environment with the existing project dependencies:

```bash
python -m unittest discover -s tests -p 'test_v5_optimizer_evidence.py' -v
python -m unittest tests.test_v5_cost_aware tests.test_v5_inventory_constraints tests.test_v5_integrated_inventory tests.test_v5_cost_aware_candidates -v
python -m unittest tests.test_v5_integrated_rolling tests.test_v5_optimality_reporting tests.test_v5_rolling_audit -v
```

Record actual counts of PASS, FAIL, ERROR, SKIP. Skipped source-dependent tests are **not** passing real-source validation. Do not report a test as passed unless it ran. If dependencies prevent running, name the missing dependency or file.

**C — Source preflight (real source files only).** The original V5 source bundle is not in the public repository. Ensure the four exact files required by `src/daily_planning/integrated_input/loader.py::V5_FILES` are available in the operator's approved `data_dir`. Provide explicit policy and pilotage context. The example `config/v5_execution.example.json` is only a skeleton; never present it as validated real policy. Run:

```bash
python scripts/v5_audit/audit_optimizer.py --mode preflight --config input/v5_execution.json
```

Acceptance for **source preflight only**: validated source hashes/structure with 140 calls, 11 physical berths, 3 pooled tanks, and 15 hard-fixed ETBs. This is NOT a successful optimization or operational approval. Report a blocker when files, coverage, policies or hashes are missing; do not silently substitute V3, a mock, an invented vessel, an invented departure window, or a generated flow series.

**D — Execute only S5** (five source calls, two 72-hour planning windows, advancing by 24 hours). Do not modify the rolling algorithm:

```bash
python scripts/v5_audit/audit_optimizer.py --mode run --config input/v5_execution.json --seconds-per-window 60
```

The CLI calls the existing `execute_scale` in `INTEGRATED_COST_AWARE_V1` mode (integer-cent demurrage objective, exact event-balance tank constraints, automatic ETB candidates and cost-aware expansion). It creates a fresh run directory under `results/v5_optimizer_audit_runs/`; no existing checkpoint may be overwritten.

If an approved S5 run already exists under this output root, audit it without rescheduling:

```bash
python scripts/v5_audit/audit_optimizer.py --mode audit-existing --config input/v5_execution.json --run-id ACTUAL_RUN_ID
```

**E — Inspect the evidence and independent replay.** The CLI must require all of these before `PASS_S5_INTEGRATED_VALIDATION_ONLY`:

1. Real-source V5 preflight passed, exact source fingerprint matches the run.
2. The S5 run reported `REQUESTED_WINDOWS_VALIDATED` with exactly two complete, structurally consistent checkpoints.
3. At least one nonempty window actually selected vessels; an all-empty run does not demonstrate optimization.
4. Selected call IDs remain inside the requested five-call scope; no duplicate call in a window.
5. Each hard-fixed ETB remains unchanged; ordinary ETBs do not precede ETA.
6. Each selected vessel has a berth and `SETTLED` per-call contract cost with a nonnegative integer-cent value; the exact per-call sum equals the window selected cost, reported CP-SAT objective and window summary.
7. No stale or unvalidated expanded candidate domain is reported as globally `OPTIMAL`.
8. The **original** `scripts/v5_full/verify_resume.py` independently replays the full saved chain and returns `independent_replay=PASS` and `REQUESTED_WINDOWS_VALIDATED`.
9. Rolling window costs are flagged **NOT ADDITIVE**; use the existing unique completed/ongoing accounting state instead of summing overlapping 72-hour projections. A pending/unresolved cumulative amount is `null`, not zero.

The additional evidence checker is intentionally not an independent physics or stock simulator. Only the existing source-verified Stage-4 replay can verify those layers. Even an S5 PASS is a **restricted synthetic projection**, never a production recommendation, operator approval, full-port feasibility proof, or 58-day global minimum.

## 3. Failure triage — no quiet relaxation

Classify every blocker by subsystem and show the first failing run/window/call/berth/tank when available:

- `MISSING_INPUT`, `INVALID_SCHEMA`, source/hash discrepancy → input provenance.
- `UNSUPPORTED_OPERATIONAL_POLICY`, 4-1/4-2, low-GRT/deep-draft, 02:00–08:00, missing departure pilotage windows → physical policy gaps.
- `NO_FEASIBLE_GENERATED_OPTION`, solver `INFEASIBLE`, timeout, or failure to expand → candidate-domain/solver behavior; do **not** infer all-time infeasibility.
- `INVENTORY_OUT_OF_BOUNDS`, transfer quantity or throughput errors, missing 6h flow coverage → exact inventory constraint/coverage.
- `CLOCK_START_AUDIT_PENDING`, `SETTLEMENT_UNRESOLVED`, Laycan To uncertainty, mismatched integer cents → contractual accounting.
- Checkpoint fingerprint/state changes, lock/carryover differences → rolling and replay errors.

There are 40 previously flagged historical clock-start divergences from a **different** workbook; V5 lacks the fields required to revalidate them. Do not quietly accept or force-zero those contracts. `CLEAR_WEATHER_BASELINE_V1` is a no-interruption **synthetic** pricing assumption, not an actual contract settlement. Synthetic operating policy fixtures are allowed only when explicitly segregated from real source claims. Never invent physical rules, pilotage windows, external flows or source evidence to obtain a PASS.

## 4. Correctness checks and reporting

For the task report, include:

- Commit/branch and all changed file paths; prove that all changed files are in the above additive scope.
- Exactly which tests actually executed, with pass/fail/error/skip totals and commands.
- Source file fingerprints and cardinality counts when available. **Never paste private input contents or entire contracts into the report.**
- Gate/window IDs, selected calls, FIX count preserved, chosen ETBs/berths, status of independent physical/inventory/settlement replay, runtime and candidate counts when available.
- Total selected cost in integer cents, per-vessel subtotal equality, and whether the final finite domain has a proof, a validated incumbent only, or no validated result. Explain any optimality gap without pretending it proves global optimality.
- Explicit `BLOCKED` classification when the input bundle/policy is not available; do not fabricate an S5 run.
- Next smallest code fix if and only if a reproducible bug is established. Do **not** implement that fix in this audit task without approval.

Final answer to the operator must be in **Korean** with a clear conclusion: `검증 통과`, `실행됐지만 검증 불완전`, or `입력/정책 부족으로 실행 불가`. State clearly that this audit has not touched PostgreSQL and has not established global 140-vessel optimality.

## 5. Stop condition

Stop after S5 auditing. Do not begin S10/S20/R7/R14/FULL140, rework tank inputs, alter contractual rules, create a DB migration, implement a UI, or start a new PR unless asked. Do not commit/push. Preserve all diagnostic artifacts and the exact executed commands for reproducibility.
