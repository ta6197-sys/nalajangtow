"""Acceptance tests for the zero-shock Risk Engine replay bridge."""
from __future__ import annotations

import unittest
from pathlib import Path

from weather_optimizer import WeatherSeries

from src.daily_planning.context import signature
from src.daily_planning.evaluation import evaluate_baseline
from src.daily_planning.input_loader import fingerprint
from src.daily_planning.risk.replay import (
    NO_SHOCK_BASELINE,
    evaluate_scenario,
)


ROOT = Path(__file__).resolve().parents[1]
WEATHER_PATH = ROOT / "input/weather_for_optimizer_30min.csv"


def snapshot():
    contract = {
        "notice_hours": 0,
        "early_start_if_work": "N",
        "allowed_minutes": 0,
        "daily_demurrage_usd": "1440",
        "nor_valid": "Y",
        "day_mode": "SHINC",
        "weather_policy": "EXCLUDE",
        "post_demurrage_exceptions": "NONE",
        "despatch_policy": "NONE",
        "holidays": [],
    }
    raw = {
        "experiment_key": "risk-replay-test",
        "as_of_at": "2026-11-01T00:00:00+09:00",
        "contracts": [{
            "contract_id": "ct-1", "allowed_minutes": 0,
            "demurrage_daily_cents": 144000, "contract_terms": contract,
        }],
        "calls": [{
            "call_id": "RISK-001", "contract_id": "ct-1",
            "nor_at": "2026-11-01T00:00:00+09:00",
            "available_at": "2026-11-01T00:00:00+09:00",
            "work_minutes": 120, "cargo_quantity_kg": 120000,
        }],
        "berths": [{"berth_id": "BERTH-01", "is_active": True}],
        "call_states": [{
            "call_id": "RISK-001", "status": "WAITING", "berth_id": None,
            "remaining_work_kg": 120000, "accrued_demurrage_cents": 0,
            "contract_clock_state": {"remaining_work_minutes": 120},
        }],
        "commits": [], "previous_tentative_assignments": [],
        "weather": fingerprint(WEATHER_PATH),
    }
    raw["snapshot_hash"] = signature(raw)
    return raw


class RiskReplayTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = snapshot()
        self.weather = WeatherSeries.from_csv(WEATHER_PATH)
        self.plan = [{
            "call_id": "RISK-001", "berth_id": "BERTH-01",
            "start_at": "2026-11-01T00:00:00+09:00",
        }]
        self.baseline = evaluate_baseline(self.snapshot, self.plan, self.weather)["evaluation"]
        self.candidate = {
            "candidate_id": "candidate-a", "input_snapshot_hash": self.snapshot["snapshot_hash"],
            "cost_cents": self.baseline["summary"]["demurrage_increment_to_completion_cents"],
            "wait_minutes": self.baseline["summary"]["total_wait_minutes"],
            "vessel_results": self.baseline["vessel_results"],
        }

    def test_no_shock_replay_matches_actual_baseline_exactly(self):
        result = evaluate_scenario(
            self.snapshot, self.candidate, {"scenario_id": NO_SHOCK_BASELINE},
        )
        self.assertEqual(result["cost_cents"], self.candidate["cost_cents"])
        self.assertEqual(result["wait_minutes"], self.candidate["wait_minutes"])
        self.assertEqual(result["completion_at"], self.baseline["summary"]["last_completion_at"])
        self.assertEqual(result["vessel_results"], self.candidate["vessel_results"])

    def test_shocks_and_stored_baseline_mismatches_fail_closed(self):
        zero = evaluate_scenario(self.snapshot, self.candidate, {"scenario_id": "SYNTHETIC_RISK_V1-1"})
        self.assertEqual(zero['status'], 'SUCCEEDED')
        self.assertEqual(zero['cost_cents'], self.candidate['cost_cents'])
        with self.assertRaises(ValueError):
            evaluate_scenario(self.snapshot, self.candidate, {
                "scenario_id": NO_SHOCK_BASELINE,
                "call_shocks": {"RISK-001": {"available_at_shift_minutes": 1}},
            })
        self.candidate["cost_cents"] += 1
        with self.assertRaises(AssertionError):
            evaluate_scenario(self.snapshot, self.candidate, {"scenario_id": NO_SHOCK_BASELINE})


if __name__ == "__main__":
    unittest.main()
