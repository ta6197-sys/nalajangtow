"""Unified report safety and regression tests. No actual DB writes."""
import unittest
from unittest.mock import patch
from src.daily_planning.integrated_v3.validation_runner import (
    validate_phase4_result, validate_phase4_rolling, render_markdown)


class UnifiedPhase4ValidationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"fixed_process_flows": {}}
        self.policy = {"policy_id": "SYNTHETIC"}
        self.at = "2026-11-03T00:00:00+09:00"
        self.result = dict(status="TIME_LIMIT", pricing_policy="CLEAR_WEATHER_BASELINE_V1",
                           elapsed_seconds=12.5, domain_exhausted=False,
                           iterations=[{"demurrage_cents": 900}, {"demurrage_cents": 450}],
                           incumbent={"total_demurrage_cents": 450,
                                      "tank_validation": {"projection_end_at": "2026-11-04T00:00:00+09:00"}})

    @patch("src.daily_planning.integrated_v3.independent_validator.validate_simulation_candidate")
    @patch("src.daily_planning.integrated_input.validator.validate")
    def test_saved_valid_flag_is_not_trusted(self, preflight, verifier):
        preflight.return_value = {"data_preflight_pass": True}
        verifier.return_value = {"status": "INVALID", "issues": [{"code": "INVENTORY_REPLAY_MISMATCH"}]}
        source = dict(self.result, independent_validation={"status": "VALID"})
        report = validate_phase4_result(self.snapshot, source, policy=self.policy, as_of_at=self.at)
        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["metrics"]["validated_incumbent_present"])
        self.assertEqual(report["issues"][0]["code"], "INVENTORY_REPLAY_MISMATCH")

    @patch("src.daily_planning.integrated_v3.independent_validator.validate_simulation_candidate")
    @patch("src.daily_planning.integrated_input.validator.validate")
    def test_fresh_replay_allows_synthetic_pass_not_operational_approval(self, preflight, verifier):
        preflight.return_value = {"data_preflight_pass": True}
        verifier.return_value = {"status": "VALID", "issues": []}
        report = validate_phase4_result(self.snapshot, self.result, policy=self.policy, as_of_at=self.at)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["metrics"]["best_cost_cents"], "450")
        self.assertEqual(report["metrics"]["improvement_cents"], "450")
        self.assertTrue(report["metrics"]["validated_incumbent_present"])
        self.assertEqual(report["checks"]["operational_commitment_authority"]["status"], "NOT_RUN")
        self.assertFalse(report["operational_approval"])
        self.assertIn("450", render_markdown(report))

    @patch("src.daily_planning.integrated_input.validator.validate")
    def test_timeout_without_incumbent_is_incomplete(self, preflight):
        preflight.return_value = {"data_preflight_pass": True}
        report = validate_phase4_result(
            self.snapshot, dict(self.result, status="TIME_LIMIT_NO_INCUMBENT", incumbent=None),
            policy=self.policy, as_of_at=self.at)
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(report["checks"]["independent_candidate_replay"]["status"], "NOT_RUN")

    @patch("src.daily_planning.integrated_v3.independent_validator.validate_simulation_candidate")
    @patch("src.daily_planning.integrated_input.validator.validate")
    def test_invalid_source_blocks_replay(self, preflight, verifier):
        preflight.return_value = {"data_preflight_pass": False, "issues": [{"code": "FLOW_GAP"}]}
        report = validate_phase4_result(self.snapshot, self.result, policy=self.policy, as_of_at=self.at)
        self.assertEqual(report["status"], "FAIL")
        verifier.assert_not_called()

    @patch("src.daily_planning.integrated_v3.validation_runner.validate_phase4_result")
    def test_rolling_stops_on_unverified_first_day(self, verifier):
        verifier.return_value = {"status": "INCOMPLETE"}
        rolling = {"status": "STOPPED_UNRESOLVED", "steps": [
            {"as_of_at": self.at, "result": {"incumbent": None}},
            {"as_of_at": "2026-11-04T00:00:00+09:00", "result": {"incumbent": None}}]}
        report = validate_phase4_rolling(self.snapshot, rolling, policy=self.policy, expected_days=58)
        self.assertEqual(report["validated_days"], 0)
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(len(report["steps"]), 1)

    def test_empty_rolling_result_is_incomplete(self):
        report = validate_phase4_rolling(self.snapshot, {"steps": []},
                                         policy=self.policy, expected_days=58)
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(report["issues"][0]["code"], "NO_STEPS")


if __name__ == "__main__":
    unittest.main()
