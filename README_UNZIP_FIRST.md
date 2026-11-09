# Docktimize V2 — additive Phase 4 tank foundation

This archive is intended to be unpacked at the **repository root** of:
`https://github.com/ta6197-sys/nalajangtow`.

Base commit checked when built: `1be95988c77bcb5205a0cda374c1cf765b14bf7b`
(2026-11-09, PR #24 merged). The repository may have changed since then.

**No existing repository file was included in this ZIP.** Contents are new,
opt-in, read-only components for tank feasibility, not a production solver.

## Safe procedure on your local clone

```bash
git status --short                # confirm or stash unrelated local work
git switch main
git pull --ff-only origin main
git switch -c feat/v2-phase4-tank-foundation
unzip -l /path/to/docktimize_v2_phase4_additive.zip  # inspect contents
unzip -n /path/to/docktimize_v2_phase4_additive.zip -d .
git status --short
python -m unittest discover -s tests -p 'test_v2_tank_inventory.py' -v
python scripts/check_tank_fixture_v1.py
git add README_UNZIP_FIRST.md docs/V2_PHASE4_TANK_KIT.md docs/V2_PHASE4_CODEX_HANDOFF.md \
    schemas/tank_network_v1.schema.json schemas/tank_schedule_v1.schema.json \
    src/daily_planning/tank tests/fixtures/tank_v1 tests/test_v2_tank_inventory.py \
    scripts/check_tank_fixture_v1.py
git commit -m "feat: add isolated V2 tank inventory feasibility foundation"
git push -u origin feat/v2-phase4-tank-foundation
```

`unzip -n` refuses to overwrite existing files. If the current GitHub branch
already has any of these filenames, **stop and inspect a diff before merging**.
Do not `git add .` when unrelated work is in progress.

For handoff to Codex, read `docs/V2_PHASE4_CODEX_HANDOFF.md`.

## Test status when packaged

14 isolated `unittest` tests passed. The 24-hour synthetic fixture returns
`FEASIBLE`, 30 tanks, 49 grid points per tank and an explicit **false**
operational-approval flag. This does not certify the actual SK facility or
production database. Entire legacy repository tests were not run in this
packaging environment.

The source-backed switching/pilotage/FIX restrictions in the latest GitHub
Phase 3 remain incomplete; do not describe this kit as resolving them.
