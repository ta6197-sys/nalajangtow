# Installation and real-data validation

## Prerequisites

- Repository: `https://github.com/ta6197-sys/nalajangtow`, based on commit `a90990292ff34d93b340f36aa8445bda022e57df` or a later clean main carefully reviewed for conflicts.
- Python 3.12, OR-Tools, openpyxl, and the existing repository dependencies installed.
- Original V5 source bundle present at one approved folder. The exact four filenames are declared in `src/daily_planning/integrated_input/loader.py` (`V5_FILES`).
- Explicit validated physical/berth policy JSON for all relevant berths and explicit pilotage context (official daylight CSV path, source, night permissions, per-berth-and-call departure windows).
- This ZIP does not contain the V5 original workbook, original flow data or real policy files.

## Apply

1. Put **only the package's new files** at the same relative paths under the repo root. Do **not** delete existing folders or overwrite Stage 1–4 source files.
2. Run `git status`, check `git rev-parse HEAD`, and check local changes.
3. Run `python tools/apply_integration.py --check --repo-root .`. Review the printed diff. It must show changes only to `web_app.py`, `compose.yaml` and `web/index.html`.
4. Run `python tools/apply_integration.py --apply --repo-root .` only after review.
5. Run source-independent unit tests and the existing regression suite.

## Configure input

Create `input/v5/` and place the four original V5 files with **exact** filenames there. Copy `config/v5_execution.example.json` to `input/v5_execution.json` and use the intended source paths. Provide `input/v5_physical_policy.json` and `input/v5_pilotage_context.json` from verified operational assumptions; the example deliberately does not provide fabricated values.

Copy `config/v5_preview_scenarios.example.json` to `input/v5_preview_scenarios.json`. This opt-in file registers the **execution_config path on the server**. Without it, the browser can view saved results but cannot start new simulations.

When using Docker, absolute `/app/...` paths shown in the example are valid only in the container. For a host-side Python CLI, replace those with host paths. Ensure `pilotage_context.daylight_path` resolves to the actual available CSV.

## Source validation

```bash
python scripts/v5_full/run_stage.py --config input/v5_execution.json --preflight-only
```

Expected: `data_preflight_pass: true` and explicit source policy warnings. This confirms **input structure and source hashes**, not operational feasibility.

## Staged run (one gate at a time)

```bash
python scripts/v5_full/run_stage.py --config input/v5_execution.json --gate S5 --seconds-per-window 60
python scripts/v5_full/run_stage.py --config input/v5_execution.json --gate S10 --seconds-per-window 60
python scripts/v5_full/run_stage.py --config input/v5_execution.json --gate S20 --seconds-per-window 60
```

Each creates a new `results/v5_preview_runs/v5-<uuid>/` directory with Stage-4 manifest/checkpoints and `execution_report.json` (or an explicit error status). No existing run is overwritten. `--gate R7`, `--gate R14`, `--gate FULL140` are available only once prior gates pass and verified input coverage is sufficient.

To automate sequential gates and stop at the first failure:

```bash
python scripts/v5_full/run_gates.py --config input/v5_execution.json --through S20
```

To compare candidate caps, using the same source and policy:

```bash
python scripts/v5_full/benchmark_caps.py --config input/v5_execution.json --gate S5
```

## Structural audit and independent original replay

Use the printed run ID:

```bash
python scripts/v5_full/audit_run.py --run-id v5-REPLACE_ME
python scripts/v5_full/verify_resume.py --config input/v5_execution.json --run-id v5-REPLACE_ME
```

`audit_run.py` checks structural fingerprints and boundary continuity; `verify_resume.py` performs **actual Stage-4 physical, inventory and settlement replay** only after all requested checkpoint files exist. It refuses partial runs, because resume could otherwise create new state.

## Web

```bash
docker compose -p solver-daily up -d --build web
```

Open `http://127.0.0.1:8765/v5-preview`. Use the approved scenario registry to execute 3- or 5-vessel, 2- or 3-window simulations. Results from the CLI and browser share `results/v5_preview_runs/`. If the Docker web process cannot access the same data paths, it cannot execute the run and must show a failure.

The existing web A/B/C data and database are unchanged by preview operations.

## Regression and unit tests

```bash
python -m unittest discover -s tests -p 'test_v5_full_*.py' -v
node --test tests/web-v5-preview.test.cjs
python -m unittest tests.test_v5_optimality_reporting tests.test_v5_integrated_rolling -v
python -m unittest discover -s tests -p 'test_*.py'
```

Report PASS/FAIL/SKIP separately. Some original tests require source files outside the repository and may skip; skipped tests are not proof of success.
