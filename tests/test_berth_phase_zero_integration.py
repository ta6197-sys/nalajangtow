"""Stage4 opt-in zero-duration compatibility and CP-SAT interval tests.

No database connection is opened. A distinct PostgreSQL end-to-end smoke test
is still required before the experimental feature can be considered complete.
"""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from ortools.sat.python import cp_model

from src.daily_planning.berth_phases import (
    ZERO_PHASE_CONFIG, apply_phase_policy, occupancy_bounds,
    phase_policy_for_experiment, validate_zero_phase_policy,
)
from src.daily_planning.evaluation import interval_overlap
from src.daily_planning.exact_search import make_master


class ZeroPhaseIntegrationTests(unittest.TestCase):
    def row(self, cid, start, end):
        return {
            "call_id": cid, "berth_id": "BERTH-01",
            "work_start_at": f"2026-11-04T{start}:00+09:00",
            "completion_at": f"2026-11-04T{end}:00+09:00",
        }

    def domain(self, a, b):
        return {
            "begin": datetime.fromisoformat("2026-11-04T00:00:00+09:00"),
            "berths": ["BERTH-01"], "fixed": [], "fixed_cost": 0, "fixed_wait": 0,
            "options": [{"row": row, "cost_lb": 0, "wait_lb": 0} for row in (a, b)],
            "groups": [[0], [1]],
        }

    def test_policy_opt_in_is_separate_and_legacy_untouched(self):
        with patch.dict("os.environ", {"BERTH_PHASE_EXPERIMENT_KEY": "stage4-zero-v1"}):
            self.assertIsNone(phase_policy_for_experiment("source-daywork-v2"))
            self.assertEqual(phase_policy_for_experiment("stage4-zero-v1"), ZERO_PHASE_CONFIG)
        with patch.dict("os.environ", {"BERTH_PHASE_EXPERIMENT_KEY": "source-daywork-v2"}):
            with self.assertRaisesRegex(ValueError, "Refusing"):
                phase_policy_for_experiment("source-daywork-v2")

    def test_nonzero_phase_policy_rejected(self):
        with self.assertRaisesRegex(ValueError, "only zero-duration"):
            validate_zero_phase_policy({**ZERO_PHASE_CONFIG, "unberthing_minutes": 30})

    def test_zero_phase_keeps_legacy_cargo_boundaries(self):
        row = self.row("DEM-0001", "09:00", "12:00")
        self.assertIs(apply_phase_policy(None, row), row)
        output = apply_phase_policy(ZERO_PHASE_CONFIG, row,
                                    available_at="2026-11-04T08:00:00+09:00")
        self.assertEqual(output["berthing_start_at"], row["work_start_at"])
        self.assertEqual(output["cargo_work_start_at"], row["work_start_at"])
        self.assertEqual(output["cargo_work_end_at"], row["completion_at"])
        self.assertEqual(output["berth_release_at"], row["completion_at"])
        self.assertEqual(output["berth_occupied_minutes"], 180)
        self.assertNotIn("berth_release_at", row)

    def test_naive_engine_times_are_normalized_without_rewriting_original(self):
        row = {
            "call_id": "DEM-0001", "berth_id": "BERTH-01",
            "work_start_at": "2026-11-04T09:00:00",
            "completion_at": "2026-11-04T12:00:00",
        }
        output = apply_phase_policy(ZERO_PHASE_CONFIG, row,
                                    available_at="2026-11-04T08:00:00")
        self.assertEqual(output["work_start_at"], "2026-11-04T09:00:00")
        self.assertEqual(output["completion_at"], "2026-11-04T12:00:00")
        self.assertEqual(output["berthing_start_at"], "2026-11-04T09:00:00+09:00")
        self.assertEqual(output["berth_release_at"], "2026-11-04T12:00:00+09:00")
        self.assertEqual(occupancy_bounds(row), (row["work_start_at"], row["completion_at"]))
        self.assertFalse(interval_overlap(row,
            {"call_id": "DEM-0002", "berth_id": "BERTH-02", **{
                "work_start_at": row["work_start_at"],
                "completion_at": row["completion_at"]}}))

    def test_release_extension_blocks_cargo_nonoverlap_in_master(self):
        first = self.row("A", "09:00", "10:00")
        second = self.row("B", "10:00", "11:00")
        first["berthing_start_at"] = first["work_start_at"]
        first["berth_release_at"] = "2026-11-04T10:30:00+09:00"
        second["berthing_start_at"] = second["work_start_at"]
        second["berth_release_at"] = second["completion_at"]
        self.assertTrue(interval_overlap(first, second))
        model, _, _ = make_master(self.domain(first, second), "cost")
        self.assertEqual(cp_model.CpSolver().Solve(model), cp_model.INFEASIBLE)

    def test_zero_phase_allows_meeting_berth_intervals(self):
        first = apply_phase_policy(ZERO_PHASE_CONFIG, self.row("A", "09:00", "10:00"))
        second = apply_phase_policy(ZERO_PHASE_CONFIG, self.row("B", "10:00", "11:00"))
        self.assertFalse(interval_overlap(first, second))
        model, _, _ = make_master(self.domain(first, second), "cost")
        self.assertIn(cp_model.CpSolver().Solve(model),
                      (cp_model.FEASIBLE, cp_model.OPTIMAL))


if __name__ == "__main__":
    unittest.main()
