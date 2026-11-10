# V3 Phase 1 — isolated input adapter

Branch `feat/11berth-synthetic-integration-v3`, base `1f372e20dd3a8f43ff1629afb180932e128bc05d`. The repository path was checked using `git rev-parse --show-toplevel`; NFC/NFD-equivalent data filenames are matched explicitly. Supplied inputs reside in the sibling `../data` directory (under `team_project`), not this repository's `data` directory. No source, database, legacy scheduler or test was modified. Per the user's updated instruction, no Git commit or push is made.

## Run

Install only `requirements-input-v3.txt` in the desired environment. No database, web UI, OR-Tools, Pyomo or HiGHS is needed for ingestion.

```
python -m src.daily_planning.integrated_input.cli --data-dir ../data --output-dir results/v3_preflight --dry-run
python -m src.daily_planning.integrated_input.cli --data-dir ../data --output-dir results/v3_preflight
```

Dry-run reads input and prints validation/manifest without creating the output directory. Missing required inputs stop loading before the workbook is parsed, with the exact filename. Existing artifact folders are never overwritten: a repeat with identical inputs, code and validation reuses the artifacts; differing content is rejected. Run identity includes exact source hashes, adapter code hash and normalization version. Generation timestamp is the original artifact creation time. Results are under `results/v3_preflight/<run_id>/` and are ignored by Git; retain them locally for audit.

## Mapping and preservation

The normalized schema is `DOCKTIMIZE_NORMALIZED_INPUT_V3`, normalization `V3_NORMALIZATION_V1`. It preserves the complete optimization JSON, complete external-flow JSON, and all eleven required workbook tables including source row numbers. Workbook tables are identified by verified header sets with preferred sheet names and an unambiguous-header fallback. Physical call data is joined by case_id and vessel_id from the vessel-profile sheet. Contract IDs are the contract table's case_id only when that identity and vessel match. No ordinary ETB/ETU or baseline berth/tank becomes a hard assignment. Historical workbook ETB/work/ETU remains reference data in the source tables; these artifacts must not be passed directly to the legacy scheduler.

MT becomes exact kg via 1,000 kg per metric tonne. Volume and mass are cross-checked against the workbook's supplied bbl_per_mt; only an exact match produces an exact rational kg/bbl value with explicit synthetic conversion provenance. Tank receipt and dispatch rates remain separate. Berth rates remain MT/hour, tank rates bbl/hour. No aggregate capacity is redistributed and no mass/density is invented. Capacity is the supplied installed upper bound; an additional operating maximum is not inferred. Physical supplementary workbook fields such as PBL, inventory timestamp, and source restrictions are preserved rather than discarded. JSON timestamps are checked for integer-second, +09:00-aware Asia/Seoul representation, and workbook naive local dates are explicitly interpreted as KST. Six-hour signed flow values are immutable input, with gap/overlap checking for every tank.

Profile assertions (140 calls, 11 active berths, 30 tanks, 15 hard FIX, 6,960 flow segments, installed capacity 10,000,000 bbl and the 2026-11-03 through 2026-12-31 flow horizon) are confined to the V3 validation profile, not reusable solver limits. `validate(snapshot, profile=...)` supports small isolated fixtures. Data-preflight VALID is separate from operational feasibility, which remains NOT_EVALUATED.

## Actual source evidence

- V3 workbook SHA-256: `6ea8ee2e8adbb1face48c9f81ef4dcec262717a8641a23c7026b6c6e89c98853`
- Optimization JSON SHA-256: `91dfc6faea010abffcd377415a8c8583ab35eef953afb7a17b1e8cb7aedb24d6`
- Flow JSON SHA-256: `bdce7ebc1f4ec4d69f702b2bf6ffd19714119e94e3724d3919ff023b55428296`
- Historical parent workbook SHA-256, retained unchanged from the JSON: `41e26de9fdf0a991e21122ab3ab61a6a5a98f7ef2de07196430d260382d47724`

Actual ingestion: 140 calls/contracts/profiles, 11 berths, 30 tanks, 15 hard FIX, 6,960 six-hour flow segments; installed capacity exactly 10,000,000 bbl. All 491 SOF rows, 3 group rules, 17 restrictions and 15 policy rows are retained. Laycan labels: 96 NORMAL, 20 EARLY, 18 LATE and 6 VERY_LATE. Intentional arrival scenarios are not rejected and never activate a settlement exception.

Seven calls have JSON allowed_hours rounded to exactly six decimal places from the workbook's MT/day formula: DEM-0013, DEM-0017, DEM-0025, DEM-0048, DEM-0109, DEM-0117, DEM-0119. Both supplied values and the exact rational formula result are preserved. Only the verified six-decimal export is classified as unresolved settlement precision; any larger difference is invalid. The user explicitly selected preservation with settlement blocked. `settlement_ready` is false. No approved evaluator term was overwritten.

The dock_4142 input remains `SYNTHETIC_POLICY_V1__NOT_YET_IMPLEMENTED`; the report records UNSUPPORTED_OPERATIONAL_POLICY as pending. Data-preflight passes, but no final schedule feasibility or operating approval is claimed.

## Existing-code audit

Inspected `context`, `exact_search`, `event_candidates`, `optimization`, `evaluation`, `berth_rules`, `berth_phases`, `sk_physical_v3`, `sk_preflight`, `daylight`, `horizon_policy`, Tank V1 network/validator/inventory and Phase 4B milp/service, plus `contracts.py`, `weather_optimizer.py`, and `rolling_horizon.py`.

- The documentary `sk_physical_v3.EXPECTED_MAPPING`, its `_load_source`/`build_sk_snapshot_for_experiment`, and `sk_preflight.audit_mapping` explicitly expect eight legacy berth mappings. They are retained and are not reused as the eleven-berth physical model.
- `exact_search.make_master` and `build_domain`, and rolling helpers use supplied berth sets; solver `num_search_workers=8` is a worker count, not a berth restriction. Do not change it as part of berth generalization.
- `context.read_snapshot`, `previous_tentative`, `prepare_run` and locking helpers query/write PostgreSQL; `optimization.optimize_run`/`export_run` read or persist results. The new package imports none of them.
- Contract, weather replay, daylight simulation, physical compatibility helpers, and tank exact validation can run without DB queries. The tank V1 network is still the separate 30-tank/10-million-bbl profile with constant process flow and a single connection rate, so this adapter preserves V3's time series and directional rates in a separate schema.
- Existing berth phases support only the explicitly experimental zero-duration scheduler integration. This does not implement V3 switching, FIX ETB reservations, pilotage or SK#4 shared resources.

## Tests and Phase 2 prerequisites

New tests cover the supplied fixture, small synthetic invalid cases, missing JSON, duplicate call/vessel identity, unknown references, capacity, FIX, physical data, flow overlap/gaps, timezone, mass conversion, original-file immutability, dry-run/no output, idempotent artifact reuse, large contract mismatch and legacy eight-berth mapping. Full regression results are recorded in the final work report.

Phase 2 still needs the eleven-berth physical scheduler and all group/resource rules, protected commitments, daylight/pilotage and switching/hose phases, and FIX conflict handling. The 4142 policy rows are preserved as unimplemented source rules. Contract precision policy remains pending. Phase 3 must extend the tank model for fixed six-hour process flows and directional rates; this adapter never substitutes them into incompatible Tank V1 fields. No later phase was implemented in this change.

Final verification: 19 new tests passed. Entire repository suite ran 252 tests: 251 passed, one existing initial-state PostgreSQL integration test skipped. Command: `PYTHONPATH=/private/tmp/docktimize-phase4b-venv/lib/python3.11/site-packages:/opt/homebrew/lib/python3.11/site-packages .venv/bin/python -m unittest discover -s tests -t .` (local HTTP test server allowed). These environment paths are local test dependencies, not runtime configuration. No legacy test was changed. Latest generated artifact run: `670af17c193ab3d738703352`, normalized snapshot SHA-256 `c922bcc8fa727f33e557643b4d77eb1ca60cc05afe05cb87bf32421c7af6b2b6`. Source SHA-256s were checked before and after both dry-run and artifact generation. Phase 1's data checkpoint passes with the explicitly reported settlement and operational validation pending; it is not scheduling or settlement approval.
