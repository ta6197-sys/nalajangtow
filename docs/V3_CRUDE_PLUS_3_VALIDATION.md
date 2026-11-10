# V3_CRUDE_PLUS_3 source and feasibility evidence

This is a separate synthetic scenario, not an approved schedule. The source files are read-only. Use `--scenario V3_CRUDE_PLUS_3` with the existing V3 input CLI; the original scenario remains the default and neither scenario substitutes for a missing file. NFC/NFD filenames are matched exactly after Unicode normalization.

Reproduce the normalized artifact, comparison and limited 72-hour trial (choose a new output directory when rerunning with changed inputs/code/results):

```bash
PYTHONPATH=/private/tmp/docktimize-phase4b-venv/lib/python3.11/site-packages:/opt/homebrew/lib/python3.11/site-packages \
  .venv/bin/python -m benchmarks.v3_crude_plus_validation --output-dir results/v3_crude_plus_audit
```

The script uses the prior original normalized snapshot at `results/v3_preflight/670af17c193ab3d738703352/normalized_snapshot.json` for comparisons. The original workbook/JSON are not currently in `../data`; the cached normalized original and historical manifest are the available baseline. If they are absent, supply their paths rather than silently fabricating a baseline. The script writes immutable manifest, normalized snapshot, preflight and scenario evidence under a scenario-specific output directory; no source is overwritten. Existing artifacts are compared rather than overwritten.

## Source evidence

- Modified workbook SHA-256: `52768abb946a50f4427672c7b9f29576a5ce0a7b7db9746dcb4737f6967ab24c`.
- Modified optimization JSON SHA-256: `12b6b1ed550b362937773cd46da6fd441a932e865284513e67d9a39117a44380`.
- Six-hour flow JSON SHA-256: `bdce7ebc1f4ec4d69f702b2bf6ffd19714119e94e3724d3919ff023b55428296`, matching the prior source artifact and normalized flow segments.
- The modified optimization JSON's `source_workbook_sha256` is also `52768abb946a50f4427672c7b9f29576a5ce0a7b7db9746dcb4737f6967ab24c`; the manifest preserves this value as recorded in the source. The previous scenario's historical parent hash was different and is not substituted.
- Normalized snapshot SHA-256: `e67ff248d1281e7a5774272971e4eaf2d906bef6cdb87f9fb77cadbdff0bc78e`.

Strict V3 data preflight: 140 calls, 11 active berths, 30 tanks, 15 FIX calls and 6,960 six-hour segments; data validation passes. Seven older contract precision items and one operational policy remain pending. Comparing normalized source records against the prior snapshot found exactly the three expected call cargo/work-bound changes, no ETA/FIX change, identical 491 SOF rows, and unchanged tank/berth masters. The normalized workbook ETA table changes only the three cargo mass fields; historical ETB/ETU fields remain unchanged. Other original historical settlement-label sheets outside the captured normalized tables cannot be independently compared from the available baseline and remain unverified.

| Call | Old bbl | New bbl | Difference bbl | Old MT | New MT | Fixed allowance hours |
|---|---:|---:|---:|---:|---:|---:|
| DEM-0144 | 175,920 | 225,764 | 49,844 | 24,000 | 30,800 | 36 |
| DEM-0084 | 366,500 | 456,659 | 90,159 | 50,000 | 62,300 | 24 |
| DEM-0060 | 366,500 | 457,392 | 90,892 | 50,000 | 62,400 | 72 |

Mass, kg and bbl reconcile exactly with source conversion factors; each revised cargo mass is below the vessel's DWT, and each has four documentary-compatible berths (BERTH-07/08/09/11). This is not proof of complete vessel loading, draft or berth/tank scheduling feasibility. No cargo or original contract allowance was recalculated to force feasibility.

Aggregate crude accounting, **not an inventory schedule**: initial 2,014,000 + signed external net inflow 29,940 + discharge 4,452,975 − loading 6,318,460 = ending 178,455 bbl. Aggregate minimum is 178,000 bbl, leaving 455 bbl. This aggregate does not pool inventory across tanks or establish time-dependent feasibility. The no-vessel counterfactual through first crude discharge ETA DEM-0144, 2026-11-07 05:15 KST, has no crude tank bounds violations, but excludes actual earlier loadings and is not an execution scenario.

## 72-hour integrated trial

The versioned existing synthetic policy, source transfer rates and automatically generated finite ETB anchors were used from 2026-11-03 00:00 KST, with 15 seconds and five iterations. Six calls are in the start window. The first berth proposal reached CP-SAT `OPTIMAL` **for its zero-objective, finite candidate model only**. Tank allocation returned `INVALID` and the coordinator returned `UNRESOLVED_VALIDATION` after one proposal: 56 inventory-bound reports and three unresolved same-tank-concurrency reports. These are candidate-specific, not an infeasibility proof.

- TANK-25 (LPG): at 2026-11-04 13:00 KST, 185,925.5 bbl versus maximum 185,000 bbl.
- TANK-30 (CRUDE): at 2026-11-05 22:30 KST, 2,527.22 bbl approximately versus minimum 11,000 bbl.
- Same-tank concurrency lacks an approved policy: TANK-30 / DEM-0011 and DEM-0047, and TANK-29 / DEM-0022 and DEM-0129.

The current tank selection search stops on this policy ambiguity and has not exhausted assignments. Each in-window call had only one ETB anchor in this trial; candidate coverage is insufficient to infer infeasibility. External weather, exact settlement, full horizon inventory/rolling carryover, and all 140 calls remain unverified. Settlement cannot be recalculated authoritatively without a validated full event timeline and unresolved contract precision rules; no historical labels were used as new results. No overall feasibility, integrated optimality or operational readiness is asserted.

## Follow-up: tank allocation search

The previous implementation stopped at the first assignment whose same-tank concurrency policy was unspecified. It now continues to explore other exact complete assignments. A selection whose only problem is unresolved concurrency is retained as policy-dependent; if no independently valid alternative exists, exhausting choices returns `POLICY_UNRESOLVED`, not `INFEASIBLE`. Timeout also cannot establish infeasibility. No concurrency policy is inferred.

The optional, default-off `inventory_cuts=True` setting enables `EXACT_TANK_MEMBERSHIP_V1`. When the exact validator identifies an inventory-bound violation, it excludes the precise membership set of that tank across the finite supplied transfer candidates. With initial inventory, fixed process flow and per-vessel timing/rates unchanged, the same tank membership reproduces the same invalid trajectory. Other assignments, including adding another vessel to that tank, are not excluded. Independent exact validation still checks every returned candidate. It is a search reduction for the fixed current candidate, not a global physical infeasibility cut or a production default.

At the same nominal 30-second budget, one trial without cuts timed out without a validated incumbent; a separate trial with cuts found a berth-and-tank-only result after 12 tank proposals and 16 exact membership cuts. This is one observation, not a statistical performance claim. The validated result has six assignments, all 30 tank inventory trajectories checked over the projection, 30 inventory values at 24 hours and one in-progress carry record. It uses the existing synthetic phase policy and finite ETB candidates; the result has no overlapping same-tank operations requiring the unresolved concurrency policy. Contract settlement, external weather, complete Freeze/commitments, full scenario/rolling coverage, actual schedule optimality and operational approval remain pending. It does not cover the later modified crude calls and does not establish full-period scenario feasibility.

The machine-readable trial includes the candidate berth rows, tank assignments, inventory extrema and carried state:
`results/v3_crude_plus_search_cuts_evidence/d40227f4bd9026ffd33e47df/scenario_validation_report.json`.
Reproduce with a new output directory using `--time-limit 30 --inventory-cuts` on `benchmarks.v3_crude_plus_validation`; runtime and time-limited outcomes may differ by machine.

## 24-hour projection carryover reconciliation

`reconcile_projection(snapshot, prior, *, same_tank_policy=None)` replays the same validated tank plan after its 24-hour boundary and compares each exact inventory event point. It verifies the snapshot and policy fingerprints, original full transfer definitions, tank assignment, carried remaining cargo, and inventories. Tank results now retain `transfer_definitions` and projection boundaries for a reproducible continuation; old results without those fields fail explicitly. This is read-only reconciliation of projections, not an observation, fresh optimization, schedule confirmation or transactional execution.

In a synthetic-policy V3_CRUDE_PLUS_3 trial from 2026-11-03 00:00 KST, the first 24-hour boundary was 2026-11-04 00:00 KST. DEM-0103 remained in progress on BERTH-01 / TANK-24 with exactly `11367/2` = 5,683.5 bbl remaining. Replaying the rest of the same six-vessel plan through 2026-11-06 15:33:36 produced 3,885 event/report points across 30 tanks and **zero inventory differences**. The later three modified crude vessels were outside this tested initial plan. This does not validate replanning, new observations, operational commitment persistence, weather, settlement, or the entire scenario.

Reproduce with a new output filename (the script refuses to overwrite an existing result):

```bash
PYTHONPATH=/private/tmp/docktimize-phase4b-venv/lib/python3.11/site-packages:/opt/homebrew/lib/python3.11/site-packages \
 .venv/bin/python -m benchmarks.v3_crude_plus_carryover \
 results/v3_crude_plus_search_cuts_evidence/d40227f4bd9026ffd33e47df/normalized_snapshot.json \
 --output results/v3_crude_plus_carryover/continuity_v1.json
```
