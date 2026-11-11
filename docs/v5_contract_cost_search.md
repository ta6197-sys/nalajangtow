# V5 contract cost search

`optimize_v5` enumerates a finite physical candidate domain using the existing
V5 scheduler, exact inventory reoptimization and whole-schedule no-goods. Every
priced candidate has passed independent physical and exact inventory checks.
The candidate domain is frozen after its initial generation; excluded schedules,
including inventory-invalid attempts, are carried across later searches.
The default is five physical-and-inventory-valid schedule evaluations and a
single shared wall-clock budget. A noninterruptible library call can overrun
the budget; a previous validated incumbent is retained after a later timeout.

`settle_v5_call` verifies the call/contract/vessel relationship and calculates
source laytime exactly, then delegates to the existing minute or second-precision
settlement adapter. Explicit event records must be candidate-specific, sourced,
known at the planning time, coverage-attested, and consistent with the
candidate's physical work intervals. Missing evidence, clock policy or precision
support is unresolved, never zero. The second-precision onset/rounding rule is
simulation only. Gross demurrage is summed in integer USD cents; no CP-SAT
cost approximation or despatch credit is introduced.

The V5 source deliberately contains no SOF or answer labels. Historical answer
labels are never used to price a candidate. The stored V3 audit identifies 40
clock-start divergences in a different workbook; all 40 IDs exist in V5, but the
V5 workbook has no historical effective-start field, so applicability cannot be
revalidated. `BLOCK_UNVERIFIED_CLOCK` blocks those calls. Explicit
`SIMULATION_DERIVED_CONTRACT_TERMS` is synthetic-only and clearly labelled;
it does not establish that the historical discrepancy was resolved. Golden
comparison against the absent V5 SOF/labels cannot be executed.

The user explicitly approved `CLEAR_WEATHER_BASELINE_V1` for the small real V5
test only. It assumes no weather, owner fault, terminal fault or other
interruptions and uses the existing second-precision simulation ledger. It is
not operational settlement. It is refused where the verified source contains an
overlapping weather restriction. The default `EXPLICIT_EVENTS_V1` does not infer
missing future events and returns `SETTLEMENT_UNRESOLVED` where evidence is absent.

Optimality is restricted to fully priced evaluated candidates; the stronger
finite-supported-domain label is used only when that domain is exhausted and no
settlement or policy issue remains. Neither is a global optimality claim. The
physical result, tank diagnostics and all scheduling runs are retained.
