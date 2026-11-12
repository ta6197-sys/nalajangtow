# V5 BALANCED 154 — source and verification

`V5_BALANCED_154` loads exactly the two prepared JSON inputs. Original source
files are not rewritten. The main JSON includes 154 profiles and contracts,
three berth groups and 17 berth restrictions. Its 140-call `notes` text and
historical policy-status strings are stale descriptive metadata, not the
active user-confirmed V5 operating policy. Workbook provenance is a declared
source hash; workbook bytes are not required or independently verified.

The physical tank capacity remains 10,000,000 bbl. This scenario separately
applies an operating maximum of 9,500,000 bbl and the source minimum of
1,000,000 bbl in the integrated model and independent inventory replay.
External process flows are not included in vessel equipment throughput limits.

The policy and daylight inputs in the execution example are placeholders;
operators must supply independently verified, explicitly versioned inputs.
Current source flow ends 2026-12-31 00:00 KST, while FULL154 at 60 rolling
windows requires coverage through 2027-01-04 00:00 KST. No flow is invented.
The previous short-horizon test policy ends 2026-11-09 00:00 KST; R7 and later
require additional separately verified policy coverage.

The original V5_3TANK scenario and old checkpoint fingerprints remain separate.
Neither a successful input check nor a finite-domain solution establishes full
154-call scheduling, real-world operating approval, or global optimality.
