# V5 integrated rolling projection

`run_v5_rolling(..., rolling_optimizer_mode='INTEGRATED_COST_AWARE_V1')`
uses automatic V5 candidates, cost-aware candidate improvement, direct
contract-cent minimization and exact inventory constraints. Its selected
schedule must pass independent physical and inventory validation, and the
rolling adapter rechecks settlement and the exact objective before advancing.
The default `LEGACY_ENUMERATION` mode and its original checkpoint configuration
remain unchanged. No legacy fallback is used by the integrated mode.

The manifest fingerprints the integrated mode, pricing policies, candidate
limits and improvement rounds. Each 24-hour transition replays the actual
physical schedule and exact tank state, persists the existing dynamic locks and
transfer fingerprints, and separately accounts for completed and in-progress
contracts. Resume revalidates the saved state without resolving the past
window. Window costs overlap and must not be added as cumulative costs.

All test results are synthetic projections; they are not operator approval or
actual contractual settlements. A per-window finite-domain optimum is not a
global rolling-horizon optimum. No 140-vessel simulation is implied.

```sh
/private/tmp/docktimize_v5_test_env/bin/python -m unittest tests.test_v5_integrated_rolling tests.test_v5_rolling
```
