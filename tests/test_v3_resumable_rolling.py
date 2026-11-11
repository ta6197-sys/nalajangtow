"""Fast no-solver tests for the V3 resumable rolling runner."""
import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from src.daily_planning.integrated_v3.resumable_rolling import run_resumable_rolling
from src.daily_planning.integrated_input.validator import timestamp


class ResumableRollingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "session"
        self.start = "2026-11-03T00:00:00+09:00"
        self.snapshot = {"calls": [{"call_id": "A"}],
                         "fixed_process_flows": {"inventory_as_of_at": self.start}}
        self.policy = {"policy_id": "SYNTHETIC_FIXTURE"}
        self.optimize_calls = []

    def optimizer(self, snapshot, **kwargs):
        self.optimize_calls.append(kwargs["as_of_at"])
        return {
            "status": "SIMULATION_FEASIBLE", "elapsed_seconds": 0.01,
            "iterations": [{"demurrage_cents": 150}],
            "incumbent": {"total_demurrage_cents": 150,
                          "berth_schedule": [{"call_id": "A"}],
                          "tank_validation": {"inventories": {}}},
        }

    def state(self, snapshot, result, *, policy, at, prior_state):
        return {
            "boundary": (timestamp(at) + timedelta(hours=24)).isoformat(),
            "completed_rows": [], "commitments": [],
            "tank_commitments": [], "carried_transfers": [],
            "initial_inventory": {}, "previous_schedule": [],
        }

    def run_trial(self, **kwargs):
        return run_resumable_rolling(
            self.snapshot, policy=self.policy, as_of_at=self.start,
            output_dir=self.folder, days=2, seconds_per_strategy=1,
            strategies=({"event_policy": "V3_ETB_EVENTS_V1",
                         "berth_heuristic": "CRUDE_INBOUND_FIRST_V1"},),
            **kwargs)

    def patches(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch(
            "src.daily_planning.integrated_v3.resumable_rolling.optimize_72h",
            side_effect=self.optimizer))
        stack.enter_context(patch(
            "src.daily_planning.integrated_v3.resumable_rolling._validate_window",
            return_value={"status": "PASS"}))
        stack.enter_context(patch(
            "src.daily_planning.integrated_v3.resumable_rolling._next_state",
            side_effect=self.state))
        return stack

    def test_two_days_checkpoint_and_summary(self):
        with self.patches():
            result = self.run_trial()
        self.assertEqual(result["status"], "REQUESTED_WINDOWS_VALIDATED")
        self.assertEqual(result["validated_days"], 2)
        self.assertEqual(len(self.optimize_calls), 2)
        self.assertEqual(len(list(self.folder.glob("day_*.json"))), 2)
        self.assertTrue((self.folder / "summary.json").is_file())
        self.assertFalse(result["operational_approval"])

    def test_resume_replays_checkpoint_without_reoptimizing_day_one(self):
        with self.patches():
            first = self.run_trial(max_new_days=1)
            second = self.run_trial(resume=True)
        self.assertEqual(first["status"], "PAUSED")
        self.assertEqual(first["validated_days"], 1)
        self.assertEqual(self.optimize_calls, [
            "2026-11-03T00:00:00+09:00", "2026-11-04T00:00:00+09:00"])
        self.assertEqual(second["status"], "REQUESTED_WINDOWS_VALIDATED")
        self.assertEqual(second["validated_days"], 2)

    def test_resume_refuses_snapshot_change(self):
        with self.patches():
            self.run_trial(max_new_days=1)
            modified = copy.deepcopy(self.snapshot)
            modified["calls"].append({"call_id": "B"})
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                run_resumable_rolling(
                    modified, policy=self.policy, as_of_at=self.start,
                    output_dir=self.folder, days=2, seconds_per_strategy=1,
                    strategies=({"event_policy": "V3_ETB_EVENTS_V1",
                                 "berth_heuristic": "CRUDE_INBOUND_FIRST_V1"},),
                    resume=True)

    def test_resume_refuses_tampered_checkpoint(self):
        with self.patches():
            self.run_trial(max_new_days=1)
            path = self.folder / "day_001.json"
            saved = json.loads(path.read_text())
            saved["state_fingerprint"] = "TAMPERED"
            path.write_text(json.dumps(saved), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "carry state mismatch"):
                self.run_trial(resume=True)

    def test_no_incumbent_blocks_and_never_advances(self):
        with self.patches(), patch(
                "src.daily_planning.integrated_v3.resumable_rolling._validate_window",
                return_value={"status": "INCOMPLETE"}):
            result = self.run_trial()
        self.assertEqual(result["status"], "STOPPED_UNRESOLVED")
        self.assertEqual(result["next_day"], 1)
        self.assertEqual(len(self.optimize_calls), 1)
        self.assertFalse((self.folder / "day_001.json").exists())
        self.assertTrue((self.folder / "blocked_day_001.json").exists())


if __name__ == "__main__":
    unittest.main()
