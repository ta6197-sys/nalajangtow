# Docktimize Stage 3 Rework — SK 1–8 piers, SINGLE physical berth, SYNTHETIC vessels

This package **replaces the earlier `docktimize_sk_physical_stage3.zip`**. Do NOT apply both versions. It is an additive code patch intended for the current GitHub repository `ta6197-sys/nalajangtow` after Stage 2 has been merged. All cargo, DWT, LOA, draft, GT and synthetic vessel IDs made by this package are **SYNTHETIC, not verified real ship data**.

## Decisions / scope

- 8 *individual* SK physical facilities, representing each SK pier group 1 to 8:

| Virtual slot | SK facility | Selection basis (NOT utilization ranking) |
| --- | --- | --- |
| BERTH-01 | `SK#1-1` | small-product/chemical/LPG capability; not selected using verified traffic frequency |
| BERTH-02 | `SK#2-7` | broad cargo coverage and relatively high DWT within pier 2 |
| BERTH-03 | `SK#3` | single SK#3 facility, medium-range products/chemicals |
| BERTH-04 | `SK#4-1` | maximum documented DWT within SK pier 4 group |
| BERTH-05 | `SK#5-5` | maximum documented DWT within SK pier 5 group |
| BERTH-06 | `SK#6` | crude + products + LPG, 70,000 DWT max in supplied source |
| BERTH-07 | `SK#7` | 130,000 DWT max in supplied source |
| BERTH-08 | `SK#8` | 169,500 DWT max in supplied source |

- Numeric physical constraints come from the user's supplied SK facility workbook already normalized to `data/sk_facilities.json`. Some source limits are conflicting or unverified: do not interpret `PASS` as terminal/port approval.
- Cargo source string `석유저제품` at SK#7/SK#8 remains **UNKNOWN**; it is NOT automatically treated as refined products. Their explicit crude cargo text may still match crude calls.
- **1 call -> 1 berth.** No multi-berth, combined berth, cross-pier capacity pooling or shared mooring arrangement is modeled.
- CP-SAT retains its 72h planning horizon / 24h freeze, existing contract computation, PostgreSQL source records and historical results. The original `DAYLIGHT_WORK_V2` scheduling/weather assumptions remain in place, and are NOT reinterpreted as authentic SK operating restrictions.
- V3's experiment key and result folder are separate. The **same PostgreSQL source DB** is used. V3 must not write back or modify contract/port_call source data. No Alembic migration is required.

## What's in this ZIP

- `config/berth_mapping.json` — approved 8 facilities, one per pier, no SBM.
- `scripts/generate_synthetic_vessel_profiles.py` — reproducible berth-independent scenario generator; default strict A independent mode, optional B conditioned scenario generation (NOT enabled by default).
- `scripts/audit_sk_physical_v3.py` — detailed audit: all 8 facility checks per blocked call; full JSON report; exit code 2 if any call has no documented single-berth PASS.
- `src/daily_planning/sk_physical_v3.py` — strict opt-in preflight, immutable source file hashes in run snapshot, physical eligibility shared by solver/assessment/operations. Accepts profile top-level `calls[]` or earlier `profiles[]`, but never accepts a non-null target berth.
- `scripts/apply_step3_integration.py` — exact-snippet, read-first patcher; `--check` **before** `--apply`; 7 existing source files changed, then backups under gitignored `results/`.
- `compose.sk-physical.yaml` — parallel web service on port 8766 (original remains 8765).
- `tests/test_sk_physical_v3.py`, `tests/test_sk_physical_patch.py` — source compatibility unit tests and source-pattern patcher contract tests.

## 0. Prerequisites and branch

```bash
cd ~/수업자료/team_project/requirement
git status --short  # preserve local edits first
# Current GitHub main includes Stage 1 and Stage 2; run on an updated clean main.
git switch main
git pull --ff-only origin main
test -f src/daily_planning/berth_rules.py
test -f src/daily_planning/sk_preflight.py
git switch -c feat/sk-single-berth-v3
unzip -o ~/Downloads/docktimize_sk_single_berth_v3_rework.zip -d .
```

**Never overwrite an uncommitted user edit.** The new ZIP has the same filenames as the *old* Stage 3 ZIP, so start from a clean Stage-2 baseline or review/undo only the old Stage-3 changes deliberately. If your local branch contains old partial stage-3 manual edits, `--check` will stop; do not brute-force apply.

## 1. Verify 140 actual call IDs (read-only PostgreSQL operation)

```bash
mkdir -p input
# Run only if the original web container is already running:
docker compose -p solver-daily exec -T web python -c 'import sqlalchemy as sa; from src.daily_planning.database import get_engine; from src.daily_planning.models import port_call; e=get_engine(); c=e.connect(); print("\n".join(c.execute(sa.select(port_call.c.call_id).order_by(port_call.c.call_id)).scalars())); c.close(); e.dispose()' > input/call_ids.txt
wc -l input/call_ids.txt  # expected 140 for intended WORK source scenario
```

Use an existing correct `input/call_ids.txt` instead if you already extracted one. The generator/audit **refuse** unexpected counts or duplicate/missing IDs. They do not change the PostgreSQL database or invent IDs. Note: `port_call` is a shared source table, so verify it contains the 140 intended WORK calls rather than a different uploaded scenario.

## 2. Audit currently existing SYNTHETIC profiles BEFORE generating again

Check for existing files:

```bash
ls -l input/vessel_profiles.sk-v3-independent.json input/vessel_profiles.sk-v3.json 2>/dev/null || true
```

If you have `input/vessel_profiles.sk-v3-independent.json` from the earlier discussion, **do not overwrite it**. Audit it as-is:

```bash
python3 scripts/audit_sk_physical_v3.py \
  --profiles input/vessel_profiles.sk-v3-independent.json \
  --call-ids-file input/call_ids.txt \
  --expected-count 140 \
  --out results/sk_v3_incompatible_calls.json
```

Exit code `0` = every call has at least one documented **single-berth PASS**; exit code `2` = blocked, report still created. `NO_COMPATIBLE_BERTH_IN_MODEL` means *no documented matching SINGLE berth among the eight selected*, **NOT** "no facility in all of SK CLX could ever berth it." `UNKNOWN` never silently becomes PASS. The report includes each rejected facility's DWT/LOA/draft/cargo issue code. Keep these results; investigate before optimizing.

If you only have the earlier `input/vessel_profiles.sk-v3.json` (facility-targeted), do not use it as a berth-independent scenario. Use a new file from Step 3 below and preserve the old version separately for comparison.

## 3. Generate independent SYNTHETIC profiles ONLY if none exist

```bash
python3 scripts/generate_synthetic_vessel_profiles.py \
  --call-ids-file input/call_ids.txt \
  --expected-count 140 \
  --seed 42 \
  --mode independent \
  --out input/vessel_profiles.sk-v3-independent.json

python3 scripts/audit_sk_physical_v3.py \
  --profiles input/vessel_profiles.sk-v3-independent.json \
  --call-ids-file input/call_ids.txt \
  --expected-count 140 \
  --out results/sk_v3_incompatible_calls.json
```

**Mode A (chosen now): `independent`.** The generator samples a cargo and dimensional profile from an explicit arbitrary scenario distribution without inspecting any berth. It does **not** regenerate or discard incompatible calls. Any 1+ blocked calls cause the strict V3 preflight to stop **before CP-SAT**; this is expected behavior, not a solver bug. If audit reports >0 blocked, do **not** proceed to start V3 optimization; investigate the source assumptions, chosen 8-berth model scope and compatibility reasons.

**Optional future B**: `--mode conditioned --out input/vessel_profiles.sk-v3-conditioned.json` rejection-samples **against all 8 single berths as a set**, not a preassigned berth, and records `rejected_attempts`. This changes the scenario population and is **not an independent real ship sample**. It is available to test a fully feasible synthetic terminal model after a deliberate scenario decision, but is NOT the default A policy now. Strict validation is retained in either case.

### Explicit SYNTHETIC assumptions

| Field | Source / model policy |
| --- | --- |
| `cargo_type` | Uniform choice of `CRUDE`, `REFINED_PRODUCT`, `LPG` (**SYNTHETIC**) |
| `SMALL` size | Arbitrary DWT 4,000–12,000, LOA 85–135m, draft 5–9m, weight 45% |
| `MEDIUM` size | Arbitrary DWT 12,000–70,000, LOA 130–245m, draft 7–14m, weight 40% |
| `LARGE` size | Arbitrary DWT 70,000–160,000, LOA 230–285m, draft 12–17m, weight 15% |
| GT | Artificial `0.52–0.64 × DWT` placeholder; **not** a validated GT/DWT formula |
| `vessel_id` | `SYN-VSL-*`, not a genuine IMO/ship identity |

Physical characteristics are *not* validated against real vessel physics or loaded draft/cargo mass correlations. The generator assigns **no `target_berth_id`**. Each profile and top-level JSON declares `data_origin=SYNTHETIC`.

## 4. Tests and optional code integration

```bash
python3 -m unittest discover -s tests -p 'test_berth_rules.py' -v
python3 -m unittest discover -s tests -p 'test_sk_preflight.py' -v
python3 -m unittest discover -s tests -p 'test_sk_physical_v3.py' -v
python3 -m unittest discover -s tests -p 'test_sk_physical_patch.py' -v

# Check source snippets on YOUR current checkout. This makes no changes.
python3 scripts/apply_step3_integration.py --check
# If CHECK OK and you have reviewed the diff plan:
python3 scripts/apply_step3_integration.py --apply

git diff --stat
git diff -- src/daily_planning web_app.py
python3 -m py_compile src/daily_planning/{sk_physical_v3,context,exact_search,evaluation,execution,operations,optimization}.py web_app.py
```

`--apply` patches exactly 7 existing files, with source backups under `results/sk_step3_backup_*`: `context.py`, `exact_search.py`, `evaluation.py`, `execution.py`, `operations.py`, `optimization.py`, `web_app.py`. There is no direct PostgreSQL migration. `--check` fails on code drift or a partially applied patch; stop and reconcile, do not force overwrite.

**Core integration:**
- `context.read_snapshot()`: frozen `sk_physical` object for explicitly opted-in NEW experiment only, with per-call eligible single berths, full synthetic vessel profiles and SHA-256 source fingerprints.
- `context.verify_snapshot()`: check source fingerprints before replay.
- `exact_search.build_domain()`: only construct optional berth intervals where physically compatible; also validate frozen committed assignments. It does NOT claim global optimization over every minute.
- `evaluation.evaluate_baseline()`: revalidate committed and planned assignments AND filter FCFS post-72h tail to eligible single berths.
- `execution.execute_committed_window()`: protected execution berth check.
- `operations.advance_in_transaction()`: additionally verify frozen physical inputs match new same-time read.
- `optimization.py` + `web_app.py`: explicitly publish `SYNTHETIC` and scope in results/API; the HTML frontend does not gain a new warning banner automatically.

## 5. Only after audit BLOCKED=0, run isolated V3 web/Docker

The service mounts host `input/`, `config/` and `data/` read-only. This avoids `.dockerignore` preventing the physical reference file being in the image. It shares **source DB** with old web, but uses a separate `WEB_EXPERIMENT_KEY`, result folder and port. Original web remains at 8765.

```bash
docker compose -p solver-daily -f compose.yaml -f compose.sk-physical.yaml \
  up -d --build web-skphysical
curl http://127.0.0.1:8766/api/status
```

New web: `http://127.0.0.1:8766`. Original: `http://127.0.0.1:8765`. New experiment key: `sk-physical-v3`. New output: `results/sk-physical-v3/`. The DB remains one PostgreSQL; do not upload/change source contract/port_call in the new service while doing comparison experiments. Source rows are shared between experiments.

Run existing project regression/integration tests in the local Docker environment (with real SQLAlchemy + OR-Tools + Postgres) before treating this stage as completed. In particular, validate candidate A/B/C, confirmation, 24h advance, evaluation tail, idempotent retries and legacy snapshot hash behavior. These full integration tests are **NOT claimed completed** by the ZIP's independent unit suite.

## 6. Git commit only after all actual project tests pass

```bash
git status --short
git add config/berth_mapping.json compose.sk-physical.yaml docs/README_STEP3.md \
  scripts/generate_synthetic_vessel_profiles.py scripts/audit_sk_physical_v3.py \
  scripts/apply_step3_integration.py src/daily_planning/sk_physical_v3.py \
  tests/test_sk_physical_v3.py tests/test_sk_physical_patch.py \
  src/daily_planning/context.py src/daily_planning/exact_search.py \
  src/daily_planning/evaluation.py src/daily_planning/execution.py \
  src/daily_planning/operations.py src/daily_planning/optimization.py web_app.py

git diff --cached --stat
git commit -m "feat: opt-in single-SK-berth physical compatibility with synthetic preflight"
git push -u origin feat/sk-single-berth-v3
```

**Do not stage** `input/`, `results/`, `.env`, any personal source inputs. Historic `planning_run`, `schedule_commit`, contract, SOF and source `port_call` rows must remain unchanged.

## Known limitations

A PASS is **only** for documented DWT, LOA, draft and cargo checks. GT/night berthing/mooring weather/unmooring/tide+UKC/pilot/tugs/line/tank/pipeline/actual SK terminal approval are intentionally not certified. No multi-berth occupancy is modeled. Existing global `DAYLIGHT_WORK_V2` remains unrelated to specific SK mooring permission; Stage 4 must separate berthing, cargo work, and unberthing and introduce trusted environmental limits.
