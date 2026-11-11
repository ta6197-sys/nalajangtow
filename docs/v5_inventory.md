# V5 tank inventory validation

`validate_v5_inventory` maps each supplied candidate transfer to the unique tank
for its cargo group, checks the bidirectional berth connection, then invokes the
existing exact V3 inventory replay without the HiGHS allocation solver. The
V5 coordinator accepts berth work intervals, constructs exact transfer segments,
and reports inventory results; it does not validate berth scheduling feasibility.

The user confirmed that concurrent vessel receipt and dispatch are allowed in
V5. The default same-tank policy is `ALLOW`; supplying `None` explicitly leaves
overlapping transfers unresolved, and `FORBID` rejects overlapping transfers.
Gross vessel receipt and dispatch are checked separately against their respective
input limits. Fixed external process flows are integrated with their original
signs, but excluded from these vessel equipment limits. Whether those limits
also cover shared facility throughput is an unresolved operational policy.

For carryover, use a valid result's `end_state` at the next projection boundary.
The state contains exact tank inventories and records for transfers still in
progress. Pass both inventories and carried transfers, along with the unchanged
full transfer definitions, to the next projection. Fingerprints and remaining
quantities protect the transfer definitions. Bounds and completeness of supplied
inventory are checked; a caller supplying a different in-bounds inventory must
establish its provenance independently. No end state is emitted on invalid replay.

The reporting script validates the source hashes and snapshot, constructs three
small candidate transfers from the actual v5 call and berth data, checks an
arbitrary-boundary carry replay, and runs focused tests. Its physical schedule and
full-schedule feasibility fields remain `NOT_EVALUATED`. It does not run a rolling
horizon optimization or infer historical schedules.
