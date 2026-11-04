# Docktimize Stage 3 — SK 1~8부두 단일 선석 통합 패치

**This ZIP supersedes docktimize_sk_physical_stage3.zip. Do not apply both.**

- User-selected 8 physical SK facilities: SK#1-1, SK#2-7, SK#3, SK#4-1, SK#5-5, SK#6, SK#7, SK#8.
- 1 port call occupies 1 physical berth. Combined/multi-berth occupancy **NOT modeled**.
- All generated vessel IDs, cargo, DWT, LOA, draft, GT: **SYNTHETIC**, not observed real data.
- Original contracts, 140 call IDs, SOF, PostgreSQL schema, legacy results: no modifications by ZIP. Source data is only read.
- Strict A default: independently generate profiles, audit ALL calls, stop before optimization on any call with zero documented SINGLE berth PASS. Do not silently modify invalid profiles.
- Optional B (not default) conditioned generator for a separately labeled test scenario; it changes the sampling distribution and should not be represented as actual vessel traffic.
- Detailed setup, patches, tests, Docker and Git commands: `docs/README_STEP3.md`.

## Quick-start

From clean updated Stage-2 `main` branch in project root:

```bash
unzip -o ~/Downloads/docktimize_sk_single_berth_v3_rework.zip -d .
python3 -m unittest discover -s tests -p 'test_sk_physical_v3.py' -v
python3 -m unittest discover -s tests -p 'test_sk_physical_patch.py' -v
python3 scripts/audit_sk_physical_v3.py \
  --profiles input/vessel_profiles.sk-v3-independent.json \
  --call-ids-file input/call_ids.txt --out results/sk_v3_incompatible_calls.json
```

`audit` exits 2 when there are blocked calls, and produces full failure explanations. Do NOT run V3 CP-SAT until reviewing that report and obtaining a valid, transparently SYNTHETIC scenario. Once ready:

```bash
python3 scripts/apply_step3_integration.py --check
python3 scripts/apply_step3_integration.py --apply
```

The integration patch refuses mismatched source versions; no automatic force/merge. Complete local PostgreSQL+CP-SAT integration regression tests before commit.
