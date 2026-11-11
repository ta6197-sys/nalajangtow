# V5 adaptive ETB candidates

`schedule_v5(..., candidate_generation_mode='AUTO_ADAPTIVE_CANDIDATES')`
requires no ordinary `candidate_etbs`; existing validated source data, explicit
policy and pilotage context are still required. The manual mode remains the
default. Default limits, confirmed by the user, are three expansion rounds,
12 candidates per call–berth pair and 2,000 candidates overall. Callers may
adjust these limits and the shared `time_limit_seconds`.

For each compatible call and berth, initial anchors include ETA plus berthing
and pilot boarding duration, hard FIX, pilotage starts and Last POB, work-window
and source restriction boundaries, protected berth releases, and a 30-minute
grid point. Exact event timestamps are retained. Each candidate is independently
phase-replayed and validated. Rejections, generation reasons and per-pair limits
are reported. Fixed ETBs are never shifted.

An infeasible finite domain triggers bounded expansion for calls with missing
candidates or conflicting candidate pairs. Expansion reuses verified physical
options, the existing release-event index, source group/berth release times and
one additional 30-minute grid point per round. Non-pairwise resource conflicts
fall back to expanding all calls. The process stops on a physical solution,
timeout, a configured limit, or no new candidate. It is not an exhaustive search,
and finite-domain failure is not proof of global infeasibility. Inventory is
validated once against a physical candidate; an inventory failure is returned
without inventory-aware reoptimization.

The existing unresolved 02:00–08:00 rule is recorded for affected individual
candidates. An unsupported 4-1/4-2 berth pair is reported as blocked while other
statically compatible berths remain eligible; if unresolved options could affect
a failed search the result is policy pending, not a global infeasibility claim.
Protected work may be supplied as `physical_commitments`; those rows are
independently checked and included in the solver and event generation.

The deadline covers input/policy validation, candidate generation, repeated
solver calls, physical replay and inventory validation. Checks occur at phase
boundaries and within loops, so a single noninterruptible library call may
finish just after the requested time; it cannot yield a successful result after
expiration. Candidate limits bound ETB triplets, whereas the existing scheduler
may derive additional physical phase-release options from those triplets.
