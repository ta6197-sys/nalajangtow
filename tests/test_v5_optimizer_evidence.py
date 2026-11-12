"""Source-independent tests for additive V5 optimization-evidence checks.

These tests do NOT establish true V5-source or production solver feasibility.
"""
from __future__ import annotations

import copy
import unittest

from src.daily_planning.integrated_v3.v5_optimizer_evidence import (
    inspect_s5_evidence, source_evidence,
)

T0 = "2026-11-03T00:00:00+09:00"
T1 = "2026-11-03T01:00:00+09:00"
T2 = "2026-11-03T02:00:00+09:00"


def sample():
    calls = [{"call_id": f"SYN-{i:03d}", "eta_at": T0,
              "fixed_etb_is_hard": 1 <= i <= 15,
              "fix_etb_at": T1 if 1 <= i <= 15 else None}
             for i in range(1, 141)]
    snapshot = {"calls": calls,
                "berths": [{"berth_id": f"B-{i}"} for i in range(11)],
                "tanks": [{"tank_id": f"T-{i}"} for i in range(3)]}
    config = {"scope": [f"SYN-{i:03d}" for i in range(1, 6)],
              "windows": 2, "rolling_optimizer_mode": "INTEGRATED_COST_AWARE_V1",
              "objective_mode": "MIN_TOTAL_DEMURRAGE",
              "inventory_constraint_mode": "EXACT_EVENT_BALANCE_V1"}
    execution = {"status": "REQUESTED_WINDOWS_VALIDATED", "validated_window_count": 2,
                 "requested_scope_count": 5, "simulation_only": True,
                 "operational_approval": False, "full_period_global_optimality_proven": False,
                 "cumulative_demurrage_cents": None,
                 "unresolved_accounting_call_ids": [],
                 "cumulative_demurrage_scope": "UNIQUE_COMPLETED_PLUS_CURRENT_ACCRUED_WHEN_FULLY_COVERED"}
    windows = []
    for i, (cid, cents) in enumerate((("SYN-001", 123), ("SYN-002", 456)), 1):
        row = {"call_id": cid, "berth_id": "B-1", "etb_at": T1}
        opt = {"total_projected_demurrage_cents": cents,
               "optimization_status": "FEASIBLE",
               "final_candidate_domain": {"optimality_proven": False}}
        candidate = {"status": "VALIDATED", "selected_schedule": [row],
                     "total_demurrage_cents": cents,
                     "vessel_demurrage_breakdown": {cid: {"status": "SETTLED", "demurrage_cents": cents}},
                     "optimizer_result": opt}
        windows.append({"summary": {"window_demurrage_cents": cents,
                                      "final_domain_optimality_proven": False,
                                      "solver_status": "FEASIBLE"},
                        "candidate": candidate, "selected_schedule": [row]})
    return snapshot, execution, {"config": config}, windows


class SourceEvidenceTests(unittest.TestCase):
    def test_counts_and_source_preflight_pass(self):
        source, *_ = sample()
        result = source_evidence(source, {"data_preflight_pass": True, "status": "VALIDATED"})
        self.assertEqual(result["status"], "PASS_INPUT_STRUCTURE_ONLY")
        self.assertEqual(result["counts"]["hard_fixed_etbs"], 15)
        self.assertFalse(result["operational_approval"])

    def test_preflight_fails_closed(self):
        source, *_ = sample()
        source["calls"].pop()
        result = source_evidence(source, {"data_preflight_pass": False})
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("SOURCE_PREFLIGHT_NOT_PASSED", {i["code"] for i in result["issues"]})


class ScheduleEvidenceTests(unittest.TestCase):
    def evidence(self):
        return sample()

    def check(self, *args):
        return inspect_s5_evidence(*args)

    def test_finite_domain_result_is_not_global_claim(self):
        report = self.check(*self.evidence())
        self.assertEqual(report["status"], "PASS_EVIDENCE_ONLY")
        self.assertFalse(report["global_58_day_optimality_proven"])
        self.assertFalse(report["projected_window_costs_are_additive"])
        self.assertFalse(report["independent_physical_inventory_settlement_replay_performed"])

    def test_fixed_etb_change_is_rejected(self):
        data = list(self.evidence())
        data[3][0]["selected_schedule"][0]["etb_at"] = T2
        data[3][0]["candidate"]["selected_schedule"][0]["etb_at"] = T2
        result = self.check(*data)
        self.assertIn("HARD_FIX_ETB_MOVED", {i["code"] for i in result["issues"]})

    def test_wrong_cost_sum_is_rejected(self):
        data = list(self.evidence())
        data[3][0]["candidate"]["vessel_demurrage_breakdown"]["SYN-001"]["demurrage_cents"] = 0
        result = self.check(*data)
        self.assertIn("INTEGER_CENT_COST_MISMATCH", {i["code"] for i in result["issues"]})

    def test_unsupported_optimality_claim_rejected(self):
        data = list(self.evidence())
        data[3][0]["candidate"]["optimizer_result"]["optimization_status"] = "OPTIMAL"
        result = self.check(*data)
        self.assertIn("INCORRECT_OPTIMALITY_STATUS", {i["code"] for i in result["issues"]})

    def test_empty_windows_do_not_count_as_optimizer_proof(self):
        data = list(self.evidence())
        for window in data[3]:
            window["selected_schedule"] = []
            window["candidate"] = None
            window["summary"]["window_demurrage_cents"] = 0
        result = self.check(*data)
        self.assertIn("NO_NONEMPTY_OPTIMIZATION_WINDOW", {i["code"] for i in result["issues"]})

    def test_missing_scope_call_rejected(self):
        data = list(self.evidence())
        data[2]["config"]["scope"] = ["SYN-001"]
        result = self.check(*data)
        self.assertIn("S5_SCOPE_NOT_EXACTLY_FIVE_SOURCE_CALLS", {i["code"] for i in result["issues"]})

    def test_nonfixed_early_etb_rejected(self):
        data = list(self.evidence())
        data[0]["calls"][1]["fixed_etb_is_hard"] = False
        data[0]["calls"][1]["eta_at"] = T2
        result = self.check(*data)
        self.assertIn("ETB_BEFORE_ETA", {i["code"] for i in result["issues"]})

    def test_zero_cost_still_needs_settlement_status(self):
        data = list(self.evidence())
        d = data[3][0]["candidate"]["vessel_demurrage_breakdown"]["SYN-001"]
        d["demurrage_cents"] = 0
        d["status"] = "SETTLEMENT_UNRESOLVED"
        result = self.check(*data)
        self.assertIn("INVALID_PER_CALL_SETTLEMENT", {i["code"] for i in result["issues"]})

    def test_reported_global_optimality_flag_rejected(self):
        data = list(self.evidence())
        data[1]["full_period_global_optimality_proven"] = True
        result = self.check(*data)
        self.assertIn("UNSUPPORTED_GLOBAL_OPTIMALITY_CLAIM", {i["code"] for i in result["issues"]})


if __name__ == "__main__":
    unittest.main()
