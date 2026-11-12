# Codex Implementation Handoff — Docktimize V5 Full-Scale Validation and Web Preview

**Repository:** https://github.com/ta6197-sys/nalajangtow  
**Audited base `main`:** `a90990292ff34d93b340f36aa8445bda022e57df`  
**Package:** `Docktimize_V5_FullScale_Web_Code_Package.zip`  
**Suggested branch:** `feat/v5-staged-fullscale-preview`

## Your role

The attached ZIP already contains the new Python orchestration, structural checkpoint audit, CLI scripts, local read-only V5 preview API, browser frontend, tests, documentation, and a guarded existing-file integration tool.

**Do not regenerate these files from scratch.** Install them at exactly their relative paths, review the integration diff, run tests against the actual latest repository and fix *demonstrated* defects only. Preserve all current Stage 1–4 engine code, especially PR #64 integrated Rolling and PR #65 optimality reporting. Do **not** overwrite existing source files from the ZIP except through the reviewed integration tool.

## 1. Git and input precheck

1. `git fetch origin main` and `git status --short`.
2. Verify that PR #64 and PR #65 are merged. Record `git rev-parse origin/main` and the current HEAD. The package was authored for `a909902...`; **if main moved, inspect changes and anchor compatibility before applying.**
3. Preserve uncommitted work; do not reset, delete or overwrite user changes.
4. Create `feat/v5-staged-fullscale-preview` from the latest reviewed main.
5. Check whether a file supplied by the ZIP already exists in the repository. If so, inspect and merge conservatively rather than overwriting.
6. Confirm all four original V5 source files (see `V5_FILES` in `src/daily_planning/integrated_input/loader.py`) are available if attempting real execution.
7. Confirm an explicitly verified physical policy JSON, pilotage context (including separate departure windows), and official daylight input are available. **Do not fill missing operational policies with fabricated defaults.**

## 2. Verify the package integrity

Extract to a separate temporary directory, not directly over the working tree.

```bash
cd /path/to/extracted/package
sha256sum -c SHA256SUMS.txt
```

On macOS where `sha256sum` is unavailable, use `shasum -a 256 -c SHA256SUMS.txt`.

Read `MANIFEST.md`, `docs/ARCHITECTURE_AND_LIMITS.md`, and `docs/INSTALL_AND_RUN.md`.

## 3. Install only additive new files

Copy the directory contents to the following **repository-relative** destinations, preserving exact names and subdirectories:

```text
src/daily_planning/integrated_v3/v5_scale.py
src/daily_planning/integrated_v3/v5_run_review.py
src/daily_planning/integrated_v3/v5_preview_api.py
scripts/v5_full/run_stage.py
scripts/v5_full/run_gates.py
scripts/v5_full/benchmark_caps.py
scripts/v5_full/audit_run.py
scripts/v5_full/verify_resume.py
web/v5-preview.html
web/v5-preview.css
web/v5-preview.js
web/v5-preview-model.js
tests/test_v5_full_review.py
tests/test_v5_full_scale.py
tests/test_v5_full_preview.py
tests/test_v5_full_apply.py
tests/web-v5-preview.test.cjs
config/v5_execution.example.json
config/v5_preview_scenarios.example.json
docs/ARCHITECTURE_AND_LIMITS.md
docs/INSTALL_AND_RUN.md
tools/apply_integration.py
patches/README.md
```

Keep the package manifest and this handoff document in the repository documentation if desired; do not accidentally copy a second nested project root.

**Never overwrite these already merged engine files:**

```text
src/daily_planning/integrated_v3/v5_physical.py
src/daily_planning/integrated_v3/v5_rolling.py
src/daily_planning/integrated_v3/v5_rolling_optimizer.py
src/daily_planning/integrated_v3/v5_inventory_events.py
src/daily_planning/integrated_v3/v5_inventory_constraints.py
src/daily_planning/integrated_v3/v5_settlement.py
```

## 4. Review and apply the existing-file integration

The package intentionally supplies a **guarded patch generator** rather than entire legacy-web replacements. It changes only:

- `web_app.py`
- `compose.yaml`
- `web/index.html`

In the repo root, run:

```bash
python tools/apply_integration.py --check --repo-root . > /tmp/v5_preview_review.diff
```

Review the diff. Verify it adds only the V5 preview GET APIs, the bounded allowlisted POST `/api/v5-preview/start`, static page assets, a sidebar link, localhost binding and a dedicated results volume.

If any anchor fails, **do not force the patch**; examine the current file and adapt narrowly with tests.

Then run:

```bash
python tools/apply_integration.py --apply --repo-root .
python -m py_compile web_app.py
```

Do not change the legacy `/api/plan`, `/api/apply`, or `/api/operate` execution paths.

## 5. First test gate — isolated tests

Run the new package tests:

```bash
python -m unittest discover -s tests -p 'test_v5_full_*.py' -v
node --test tests/web-v5-preview.test.cjs
```

Then run the existing relevant V5 regression tests:

```bash
python -m unittest \
  tests.test_v5_cost_aware \
  tests.test_v5_inventory_events \
  tests.test_v5_inventory_constraints \
  tests.test_v5_integrated_inventory \
  tests.test_v5_integrated_hardening \
  tests.test_v5_cost_aware_candidates \
  tests.test_v5_optimality_reporting \
  tests.test_v5_integrated_rolling \
  tests.test_v5_rolling -v
```

Follow with the repository's wider unit-test suite and old web/UI tests as practical. Record **passed, failed and skipped counts**, including skipped tests caused by missing real V5 inputs. Do not call mock-based tests real integration validation.

## 6. Second gate — validated V5 source preflight

1. Supply the exact original V5 workbook, source JSON, process-flow JSON and evidence text in a dedicated source folder.
2. Prepare explicit policy and pilotage JSON files. The example config is intentionally *not* a valid operating policy.
3. Create `input/v5_execution.json` based on `config/v5_execution.example.json` and correct its paths for the execution environment.
4. Run:

```bash
python scripts/v5_full/run_stage.py --config input/v5_execution.json --preflight-only
```

The original Stage-4 input verification must pass **without mocks**. Preserve warnings, especially unresolved 4-1/4-2 phase rules, low-GRT deep-draft pilotage, 02:00–08:00 restrictions, shared throughput policy, and Laycan To settlement. **Do not bypass these restrictions or edit cargo quantities to obtain a green result.**

If source data or policy is unavailable, report `INPUT_DATA_NOT_CONFIGURED` / unsupported policy and stop the real execution gates. The source-independent unit tests can still pass.

## 7. Third gate — scale up sequentially

Run:

```bash
python scripts/v5_full/run_stage.py --config input/v5_execution.json --gate S5 --seconds-per-window 60
python scripts/v5_full/run_stage.py --config input/v5_execution.json --gate S10 --seconds-per-window 60
python scripts/v5_full/run_stage.py --config input/v5_execution.json --gate S20 --seconds-per-window 60
```

Stop on a failed gate. Diagnose whether it is a missing source/policy, infeasible finite candidate domain, exact tank stock, solver timeout, unresolved settlement, or a genuine code defect. Do not simply increase timeouts to hide a defect.

Once passed, validate longer runs:

```bash
python scripts/v5_full/run_stage.py --config input/v5_execution.json --gate R7 --seconds-per-window 120
python scripts/v5_full/run_stage.py --config input/v5_execution.json --gate R14 --seconds-per-window 120
```

Run the **FULL140** gate only after verifying future external-flow coverage and policy completeness:

```bash
python scripts/v5_full/run_stage.py --config input/v5_execution.json --gate FULL140 --seconds-per-window 120
```

IMPORTANT: The original 696 external flow records span 58 days. The 72-hour lookahead can extend past that coverage. **Do not invent missing flows, exclude blocked vessels silently, or label all 140 completed unless every scoped call is actually completed.** FULL140 requires both `REQUESTED_WINDOWS_VALIDATED` and `all_scope_calls_completed=true`.

## 8. Checkpoint replay and cost/optimality audit

Each run creates a new `results/v5_preview_runs/<run_id>/` directory. Verify both structural and independent replay:

```bash
python scripts/v5_full/audit_run.py --run-id <run_id>
python scripts/v5_full/verify_resume.py --config input/v5_execution.json --run-id <run_id>
```

The latter refuses incomplete checkpoint sequences and reuses the original Stage-4 independent physical/inventory/settlement replay. Preserve the per-window `final_candidate_domain` proof and do not equate best validated incumbent with globally proven optimum.

Compare candidate caps only on identical source, policy, scope and time-budget inputs:

```bash
python scripts/v5_full/benchmark_caps.py --config input/v5_execution.json --gate S5
```

Never sum overlapping 72-hour projected costs. The only potentially cumulative accounting is the original Rolling completed-plus-accrued accounting, when its coverage is complete.

## 9. Web end-to-end verification

Place the approved execution config and scenario registry in the configured read-only `input/` volume. The registry file `input/v5_preview_scenarios.json` points to `input/v5_execution.json` on the **server**, not to a browser-supplied file.

```bash
docker compose -p solver-daily up -d --build web
```

Open `http://127.0.0.1:8765/v5-preview`.

Verify:

1. The legacy `/` page, `/api/status`, and Swagger continue functioning.
2. Saved V5 runs load using `/api/v5-preview/runs`.
3. Window switching shows **actual** source-derived ETB, phase timestamps, berth, cost and inventory points.
4. All three V5 tanks use the exact replay points (no browser-recomputed inventory).
5. A lower projected cost is shown only when an independently validated candidate exists.
6. Final-domain unproven cases are never displayed as `OPTIMAL`.
7. Inter-window carried stock and cargo progress match original checkpoint JSON exactly.
8. A 3/5-vessel preview can be launched **only if** the administrator registered verified input and policies.
9. The preview cannot launch a 140-vessel job or mutate legacy PostgreSQL schedules.
10. Invalid path traversal, symlinks and tampered checkpoint fingerprints are rejected.

Include actual screenshots only if captured from the running frontend. Do not claim that code compilation or a blank page proves the UI works.

## 10. Final report in Korean

Report:

- Exact Git baseline and branch.
- Added files and the small existing-file changes.
- Python/Node test counts, failures, skips and actual CI status.
- Source preflight hash and policy warnings.
- Gate S5/S10/S20/R7/R14/FULL140 outcomes, **only for actually executed gates**.
- Window-by-window selected calls, generated option counts, solver statuses and validated projected costs.
- Checkpoint/independent replay results and carried cargo/inventory.
- Full-period completed-call count, unresolved calls and known operational-policy blockers.
- Memory/runtime benchmarks and cost comparison **only where evidence exists**.
- V5 dashboard URL and exact browser verification results.
- Any code defects corrected, with regression tests.
- Reproduction commands.

Do not commit, push, open or merge a PR unless the user explicitly asks. Do not claim a validated 140-call optimum unless the full source-verified runs genuinely succeeded; even then specify that finite-window Rolling is not globally optimal over the whole continuous timeline.
