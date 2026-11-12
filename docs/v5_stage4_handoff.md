# Stage 4 integrated rolling — post-merge handoff

**GitHub base SHA:** `a90990292ff34d93b340f36aa8445bda022e57df` (PR #64 already merged; PR #65 reporting fix merged afterward).

This package **does not recreate Stage 4**. Existing files that MUST NOT be overwritten from an older commit:

- `src/daily_planning/integrated_v3/v5_rolling.py`
- `src/daily_planning/integrated_v3/v5_rolling_optimizer.py`
- `tests/test_v5_integrated_rolling.py`
- `docs/v5_integrated_rolling_stage4.md`

The original Stage 4 path calls `run_v5_rolling(rolling_optimizer_mode='INTEGRATED_COST_AWARE_V1')` and uses direct contract-cent minimization, integrated exact tank constraints, cost-aware ETB expansion, per-call dynamic ETA-5h locking, 72h plan/24h step, exact carryover and checkpoint/resume. The `LEGACY_ENUMERATION` default remains available.

## Added files

- `src/daily_planning/integrated_v3/v5_rolling_audit.py`: read-only structural/fingerprint audit of a persisted Stage 4 checkpoint chain.
- `scripts/v5_rolling/audit_integrated_run.py`: CLI for the above audit.
- `scripts/v5_rolling/run_integrated_fixture.py`: actual Stage 4 optimizer + Rolling execution using the repository's **mocked synthetic test fixture** (source preflight mocked). Makes a pause/resume run and audits the saved chain.
- `tests/test_v5_rolling_audit.py`: deterministic structural audit tests.

## Commands

```bash
python -m unittest tests.test_v5_optimality_reporting -v
python -m unittest tests.test_v5_integrated_rolling tests.test_v5_rolling -v
python -m unittest tests.test_v5_rolling_audit -v
python scripts/v5_rolling/run_integrated_fixture.py --windows 3 --seconds-per-window 30
python scripts/v5_rolling/audit_integrated_run.py results/v5_rolling_runs/<existing-run-folder>/rolling
```

The fixture runner prints the exact path of the new checkpoint directory; use that path instead of guessing the run directory.

## What this audit proves / does not prove

**Proves (if PASS):** checkpoint and state fingerprints match the repository canonical JSON hash, windows are contiguous and correctly timed, saved `next_state`/summary/candidate values are internally consistent, lock and completion metadata are structurally preserved, cargo progress is nonnegative and does not regress, and Stage 3.1 optimality metadata is not obviously contradictory.

**Does NOT prove:** actual data preflight, physical feasibility, tank feasibility, contractual correctness, source-file provenance, real-world berth policy, or global 140-call optimality. Hashes are not authenticated signatures: a malicious actor can rewrite all files and recompute hashes. `run_v5_rolling(..., resume=True)` on a validated source bundle performs the stronger independent replay. The fixture runner itself explicitly mocks source preflight and must not be presented as an end-to-end verified production input run.

Window projected costs cover overlapping 72h windows. NEVER add these costs. Use existing unique-completed-plus-current-accrued accounting when coverage is complete.

## Next step

Once the new audit and existing Stage 4 regression tests pass, implement the V5 read-only preview/API in a **separate** feature branch. Read the actual saved checkpoint schema, never invent a web result or reuse the legacy 8-berth API as the V5 optimizer.
