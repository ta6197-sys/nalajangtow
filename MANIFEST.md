# Docktimize V5 Full-Scale and Web Integration Package

- **Repository:** https://github.com/ta6197-sys/nalajangtow
- **Verified main:** `a90990292ff34d93b340f36aa8445bda022e57df`
- **PR #64:** integrated Rolling Horizon already merged. **PR #65:** optimality reporting already merged.
- **Package purpose:** **new additive code** for real-data staged execution and local V5 results dashboard, not a rewrite of Stage 1–4.
- **Protected existing files:** v5 optimizer, tank, settlement, locks and rolling modules. **Never overwrite them from the archive.**
- **Existing-file integration:** `tools/apply_integration.py` uses checked exact anchors to add routes to `web_app.py`, local bind/result volume in `compose.yaml`, and one nav link in `web/index.html`.
- **Run provenance:** actual original V5 input is external; no customer source files, live operating policy files or validated full-scenario results are included.

## New files

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
patches/README.md
tools/apply_integration.py
CODEX_APPLY_INSTRUCTIONS_EN.md
MANIFEST.md
SHA256SUMS.txt
```

All paths above are **relative to repository root**. Extract separately and copy only the new files, then run the guarded integration script.

## Verification performed during package creation

- Python module and script syntax compilation.
- Pure unit tests for structural checkpoint integrity, state fingerprints, invalid optimality claims, bounded scope/configuration, API allowlists, and safe integration anchors.
- Node pure formatting tests for actual timestamp geometry, exact-source stock quantities, absent costs, unproven optimality and escaping.
- ZIP integrity and per-file SHA-256 verification.

**Not performed in the packaging environment:** actual source-verified OR-Tools integration runs against the private V5 input bundle, the entire repository regression suite, PostgreSQL/Docker browser end-to-end verification, full 140-call Rolling optimization. Codex must run and report them separately.
