# V5 physical and inventory integration

Use `schedule_v5` from `src.daily_planning.integrated_v3.v5_physical` for the
verified V5 path. Supply the original normalized snapshot, its source data
folder, an explicit list of calls in scope, finite ordinary ETB candidates, and
a complete explicit synthetic phase policy. The adapter verifies the snapshot
against the original inputs and workbook hash, reads the original berth group
and restriction sheets, and does not modify the source snapshot. All calls
remain in the source snapshot; the selected subset and unselected call IDs are
reported separately.

The pilotage context must identify a real daily sunrise/sunset CSV and its
source, explicit per-berth night-berthing permissions, and separate explicit
unberthing windows for each eligible berth/call. Inbound windows reuse the V3
pilotage derivation: below 30,000 GRT may use night only with berth permission;
30,000–49,999 GRT has last POB 90 minutes before sunset; 50,000 GRT and above
has last POB 120 minutes before sunset. The daylight hash is included in the
applied policy fingerprint. User-specified 60/120/60-minute phases and zero
extra pilot transit are required. The unresolved low-GRT/deep-draft exception and
02:00–08:00 rule fail closed when a selected test could be affected. Unberthing
windows are not inferred from inbound windows, and cargo work windows remain
separate from pilotage windows.

The CP-SAT service uses static berth compatibility including bidirectional tank
connectivity, vessel dimensions and cargo operations. It applies source
restrictions and the supplied policy, independently validates the selected
physical phases, and only then sends the same rows to the exact V5 inventory
replay. Inventory failure is reported separately and does not cause a search for
a different physical schedule. Results do not establish whole-scenario,
contract, rolling-horizon, or operational feasibility.

The existing source's 4-1/4-2 event definitions and GRT exception remain
unresolved. If a selected call can use either of those berths, the adapter
returns `UNSUPPORTED_OPERATIONAL_POLICY`; it does not silently exclude those
berths or reinterpret their rules. If none of the selected calls can use those
berths, that group cannot apply to the selected calls. Other source groups and
restrictions remain in force. A finite candidate-domain failure is not a proof
of infeasibility over all possible ETBs.

This adapter requires a fully specified policy with explicit pilotage and work
windows, phase durations and tug resources. The original input does not provide
all of those values. Synthetic policies and small selected subsets can support
integration tests, but are not real-port operating approval.
