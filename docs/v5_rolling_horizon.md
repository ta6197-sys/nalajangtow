# V5 rolling projection with dynamic locks

`run_v5_rolling` runs the existing V5 cost search over selected calls, with a
72-hour plan and 24-hour advancement. All generated state is a **synthetic
projection**, never a physical observation or automatic operational approval.
A scope of calls can be supplied for a small run; otherwise the source calls
are in scope. Hard FIX ETBs remain enforced by the existing scheduler.

The old 24-hour blanket freeze is replaced by per-call dynamic locks. At or
within five hours of ETA, an existing schedule is locked. A missing schedule
when an automatic or manual lock is required stops the run; the system does not
invent one. A `MANUAL_UNLOCK` override remains in effect until `MANUAL_LOCK` or
`AUTO` is explicitly supplied. Already-started physical work remains locked.
Caller-supplied operator-approved commitments remain locked even when a manual
unlock is supplied; this interface does not authenticate the caller or grant
approval. Simulated, manual and caller-supplied approved origins are labelled
separately. Full physical rows are supplied as hard solver commitments.

`override_events` predeclares changes by window time. For a paused run,
`manual_overrides` applies to the next new window and is persisted in its
checkpoint. Values are `AUTO`, `MANUAL_LOCK`, or `MANUAL_UNLOCK`. A previously
started transfer cannot be unlocked; its exact definition and fingerprint are
carried into the existing V5 inventory replay. A transfer whose cargo work has
finished but whose berth occupation continues is kept as a physical commitment
without replaying cargo.

For each transition, exact V5 inventory is independently replayed to T+24 and
reconciled against the 72-hour projection. Completed and remaining quantities,
ongoing transfer definitions, tank inventory and protected physical phases are
checkpointed. Completed calls are excluded from later scheduling. Empty windows
still replay fixed external flows and do not invent work.

Window demurrage is a projected 72-hour value and must not be summed across
windows. Completed calls are settled once; ongoing work has a separately
recomputed exact ledger through the boundary. A not-yet-started or otherwise
uncovered contract is explicitly pending, and cumulative demurrage is `null`
when complete nonduplicated accounting coverage is not established. Historical
40-call clock-start differences remain unresolved under the existing policy;
they are not labelled invalid contracts. The approved no-interruption baseline
is simulation only and does not override source physical restrictions.

The manifest fingerprints source, physical policy, daylight data, settlement
policy, scope and configuration. Each completed transition is atomically saved
with a checkpoint fingerprint and previous/next state fingerprints. Resume
revalidates physical commitments, exact inventory, contractual pricing and the
24-hour transition without re-running the cost search. A damaged or noncontiguous
checkpoint is rejected, and a failed or incomplete window is not advanced.
The hashes detect accidental changes; they are not cryptographic signatures or
proof of real operator approval.
