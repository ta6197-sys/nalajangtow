# Docktimize V5 — Simulation Bridge V2 and native NO_SHOCK baseline

Repository baseline: PR #87, `48891753b532e49f16d2e087affa4256519358c3`.

## Intent and explicit limits

V2 is an **additive**, versioned, auditable data contract for the next Risk Engine.
It does **not** modify the CP-SAT objective, candidate generation, physical/
inventory limits, settlement rules, rolling lock policy or source/checkpoints.
It does not implement ETA, work-duration or weather shocks; nor does it claim
that the legacy V3 risk engine already understands V5 output.

The `NO_SHOCK` verification implemented here is **V5-native no-shock parity**:
re-evaluating saved ETB/ETU/physical intervals against the independent V5
physical validator, exact tank event replay, contract settlement in integer
cents and, for complete runs, the full native rolling checkpoint resume.
These validators reuse the project's V5 code. This is NOT a separate,
independently implemented stochastic simulation engine.

## Exact sources and baseline

- Input profile: `V5_BALANCED_154_EXT_20270109`; normalized scenario: `V5_BALANCED_154`.
- Source JSON: `Docktimize_v5_선박입력_BALANCED.json`.
- Flow JSON: `Docktimize_공정유량_6h_3탱크_1000만bbl_BALANCED_20270109.json`.
- 154 calls, 15 hard FIX ETBs, 11 synthetic berths, 3 tanks,
  816 six-hour flow segments, 2027-01-10 00:00 KST exclusive flow end.
- PR #87 config: `config/v5_balanced_ext_20270109.s20_pilotage_gap.example.json`.
- Source SHA-256: `4d587107a051e83bcc3b9e80e74cbd17585e2d2b555eac1dc5fcb5faae2a7850`.
- Extended flow SHA-256: `cabe3a7eab2d1e20333c02eb1d40dfb09b58796d7c4d865eb649994b426fbfe2`.

The input files may be local/ignored: do not replace them with manufactured data.

## Files and contracts

1. `src/daily_planning/integrated_v3/v5_simulation_bridge_v2.py` — new module.
2. `scripts/v5_full/export_simulation_bridge_v2.py` — new read-only CLI.
3. `scripts/v5_full/verify_simulation_bridge_v2.py` — source/policy/rollover attestation checker.
4. `tests/test_v5_simulation_bridge_v2_pure.py` — stdlib unit tests.
5. `tests/test_v5_simulation_bridge_v2_integration.py` — native V5 integration tests.

Outputs under a **new folder** (never overwrite):

- `bridge_v2.json`: `V5_SIMULATION_BRIDGE_V2` input bundle. Contains full
  verified normalized source snapshot, original operating policy, pilotage
  context, exact daylight file bytes (base64), contract rows, source/policy/run
  fingerprints, v1 descriptors for each validated window, synthetic vessel
  states at T+24, due/admitted/deferred partition, pilotage-gap evidence,
  deferral history/waiting hours, exact cargo and contract accounting state.
- `no_shock_parity_v1.json`: native replay status, exact-cents and inventory
  comparisons, and full-chain native resume outcome if complete.

`bridge_v2.json` includes synthetic source and contract records. **Do not expose
this raw bundle through public web APIs**. Future UI should use a narrow
allowlisted projection (and appropriate authentication).

## High-confidence no-shock checks

For every validated window:
- Same saved `selected_schedule` fields and physical event replay (ETB, ETU,
  phases and work intervals). No optimizer rerun is performed.
- Exact event-level tank inventory series/extrema, transfers, and T+24 stock.
- Identical contract pricing breakdown and total cost, to the cent.
- Inventory risk category exact comparison when enabled.
- Complete due = admitted + deferred partition; no double ownership; hard FIX,
  prior scheduled or protected calls never deferred.
- Historical deferral count/waiting hours independently recomputed from source
  ETA and the prior verified state (when deferral policy is used).
- Horizon pilotage-gap certificates only apply to deferred calls. Native resume
  checks their proofs against the original sourced policy.
- Whole-request rolling `resume=True` is called **only if every requested
  checkpoint exists**, avoiding accidental creation of missing windows.

The module never sums overlapping window forecast costs. Deferred vessel
contract liability is recorded as **UNPRICED_NOT_ZERO**, never zero.

## Run commands

```bash
# 1. Validate the real input profile (requires local original JSON files).
python scripts/v5_full/run_stage.py \
  --config config/v5_balanced_ext_20270109.s20_pilotage_gap.example.json \
  --gate S20 --preflight-only

# 2. Run the new tests and full regression.
python -m unittest tests.test_v5_simulation_bridge_v2_pure -v
python -m unittest tests.test_v5_simulation_bridge_v2_integration -v
python -m unittest discover -s tests -p 'test_v5*.py' -v

# 3. Run S20 in a NEW result root if its real results have not been recorded.
python scripts/v5_full/run_stage.py \
  --config config/v5_balanced_ext_20270109.s20_pilotage_gap.example.json \
  --gate S20 --seconds-per-window 120 \
  --output-root results/v5_s20_stage1_verified_new

# 4. Export a specific previously VALIDATED run; use its actual run ID.
python scripts/v5_full/export_simulation_bridge_v2.py \
  --config config/v5_balanced_ext_20270109.s20_pilotage_gap.example.json \
  --run-directory results/v5_s20_stage1_verified_new/<RUN_ID> \
  --output-directory results/v5_bridge_v2/<NEW_EXPORT_ID> \
  --verify-no-shock

# 5. Structural attestation alone (does NOT constitute no-shock simulation).
python scripts/v5_full/verify_simulation_bridge_v2.py \
  results/v5_bridge_v2/<NEW_EXPORT_ID>/bridge_v2.json
```

The `--allow-partial` option can export verified prefixes for diagnostics.
It cannot give full NO_SHOCK baseline PASS and cannot substitute for a
completed S20 gate. Do not use it to hide S20 failure.

## Acceptance gate

- `REQUESTED_WINDOWS_VALIDATED`, validated window count matches requested;
  this is **not** evidence every vessel completed its service.
- A complete saved run passes independent native resume.
- `NATIVE_NO_SHOCK_BASELINE_PARITY_PASS`, exact phase/inventory/settlement parity.
- `bridge_v2.json` structural attestation passes and covers every vessel in
  the run scope without invented observations.
- Existing source SHA, versioned policy, FIX ETB and historical checkpoints
  remain untouched; all source/contract/deferral/fingerprint tampering tests fail closed.
- No unvalidated window, no time-limit/UNKNOWN, no missing source contracts is
  reported as successful simulation.

If S20 is still blocked, use previously validated S5/S10 as the scoped baseline
and report S20 blocking evidence. Do NOT claim S20 or FULL154 completeness.
