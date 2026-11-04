"""Stage 3: source-backed single-berth checker and SYNTHETIC scenario tests."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.daily_planning.berth_rules import load_facilities, load_mapping, check_physical_compatibility
from src.daily_planning.sk_physical_v3 import (
    EXPECTED_MAPPING, POLICY, read_profiles, build_sk_snapshot_for_experiment,
    verify_sk_snapshot, eligible_berths, assert_eligible,
    preflight_full_source_scenario,
)
from scripts.generate_synthetic_vessel_profiles import generate, make_profile
from scripts.audit_sk_physical_v3 import audit

ROOT = Path(__file__).resolve().parents[1]


class TestPhysicalV3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapping = load_mapping(ROOT / "config/berth_mapping.json")
        cls.facilities = load_facilities(ROOT / "data/sk_facilities.json")
        cls.ids = [f"DEM-{n:04d}" for n in range(1, 141)]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile_file = Path(self.temp.name) / "profiles.json"
        self.conditioned = generate(self.ids, mode="conditioned", mapping=self.mapping, facilities=self.facilities)
        self.profile_file.write_text(json.dumps(self.conditioned, ensure_ascii=False), encoding="utf-8")
        self.env = {
            "SK_PHYSICAL_EXPERIMENT_KEY": "sk-physical-v3-test",
            "SK_PHYSICAL_MAPPING_FILE": str(ROOT / "config/berth_mapping.json"),
            "SK_PHYSICAL_FACILITIES_FILE": str(ROOT / "data/sk_facilities.json"),
            "SK_PHYSICAL_PROFILES_FILE": str(self.profile_file),
        }
        self.calls = [{"call_id": cid} for cid in self.ids]
        self.berths = [{"berth_id": b, "is_active": True} for b in sorted(self.mapping)]

    def snapshot(self):
        with patch.dict(os.environ, self.env):
            return build_sk_snapshot_for_experiment("sk-physical-v3-test", self.calls, self.berths)

    def test_01_selection_matches_sk_piers_1_to_8(self):
        self.assertEqual(self.mapping, EXPECTED_MAPPING)
        self.assertNotIn("SKB#2", self.mapping.values())
        self.assertNotIn("SKB#3", self.mapping.values())
        self.assertEqual(len(self.mapping), 8)

    def test_02_generator_preserves_all_140_ids(self):
        self.assertEqual({r["call_id"] for r in self.conditioned["calls"]}, set(self.ids))
        self.assertEqual(len(self.conditioned["calls"]), 140)

    def test_03_generator_marks_every_field_synthetic(self):
        self.assertEqual(self.conditioned["data_origin"], "SYNTHETIC")
        self.assertTrue(all(r["data_origin"] == "SYNTHETIC" and r["vessel_id"].startswith("SYN-VSL-")
                            for r in self.conditioned["calls"]))
        self.assertIn("SYNTHETIC", self.conditioned["purpose"])

    def test_04_no_target_berth_is_assigned(self):
        self.assertTrue(all("target_berth_id" not in r and "berth_id" not in r
                            for r in self.conditioned["calls"]))
        self.assertIn("no preselected physical berth", self.conditioned["generator"]["selection_rule"])

    def test_05_generation_independent_of_order(self):
        other = generate(self.ids[::-1], mode="conditioned", mapping=self.mapping, facilities=self.facilities)
        self.assertEqual(other, self.conditioned)

    def test_06_changing_seed_changes_profiles(self):
        other = generate(self.ids, mode="conditioned", seed=43, mapping=self.mapping, facilities=self.facilities)
        self.assertNotEqual(self.conditioned["calls"], other["calls"])

    def test_07_independent_mode_does_not_filter_bad_vessels(self):
        a = generate(self.ids, mode="independent", mapping=self.mapping, facilities=self.facilities)
        b = [make_profile(cid, seed=42) for cid in sorted(self.ids)]
        self.assertEqual(a["calls"], b)
        self.assertEqual(a["generator"]["rejected_attempts"], 0)

    def test_08_conditioned_mode_requires_a_single_pass_berth(self):
        profiles = {r["call_id"]: r for r in self.conditioned["calls"]}
        result = audit(profiles, self.mapping, self.facilities, self.ids)
        self.assertEqual(result["summary"]["blocked"], 0)
        self.assertEqual(result["summary"]["passed"], 140)
        self.assertTrue(all(x for x in result["compatibility_matrix"].values()))

    def test_09_legacy_run_remains_unchanged_without_optin(self):
        with patch.dict(os.environ, {"SK_PHYSICAL_EXPERIMENT_KEY": ""}):
            self.assertIsNone(build_sk_snapshot_for_experiment("sk-physical-v3-test", self.calls, self.berths))
        self.assertEqual(eligible_berths({}, "DEM-0001", ["BERTH-01", "BERTH-05"]), ["BERTH-01", "BERTH-05"])

    def test_10_isolation_by_experiment(self):
        with patch.dict(os.environ, self.env):
            self.assertIsNone(build_sk_snapshot_for_experiment("source-daywork-v2", self.calls, self.berths))
            self.assertIsNone(build_sk_snapshot_for_experiment("other", self.calls, self.berths))

    def test_11_legacy_experiment_cannot_optin(self):
        with patch.dict(os.environ, {**self.env, "SK_PHYSICAL_EXPERIMENT_KEY": "source-daywork-v2"}):
            with self.assertRaisesRegex(ValueError, "Refusing"):
                build_sk_snapshot_for_experiment("source-daywork-v2", self.calls, self.berths)

    def test_12_full_140_snapshot_success_in_conditioned_mode(self):
        physical = self.snapshot()
        self.assertEqual(physical["policy"], POLICY)
        self.assertEqual(physical["synthetic_call_count"], 140)
        self.assertEqual(physical["multi_berth_occupancy"], "NOT_MODELED")
        self.assertEqual(set(physical["eligible_berths"]), set(self.ids))

    def test_13_malformed_profile_missing_is_blocked(self):
        self.conditioned["calls"].pop()
        self.profile_file.write_text(json.dumps(self.conditioned), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "VESSEL_PROFILE_MISSING"):
            self.snapshot()

    def test_14_invalid_origin_is_blocked(self):
        self.conditioned["calls"][0]["data_origin"] = "REAL"
        self.profile_file.write_text(json.dumps(self.conditioned), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SYNTHETIC"):
            self.snapshot()

    def test_15_nonnull_target_berth_is_blocked(self):
        self.conditioned["calls"][0]["target_berth_id"] = "BERTH-01"
        self.profile_file.write_text(json.dumps(self.conditioned), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "preselected"):
            self.snapshot()

    def test_16_support_previous_profiles_array_without_preselection(self):
        payload = {
            "schema_version": 1, "data_origin": "SYNTHETIC",
            "profiles": [{**r, "target_berth_id": None, "size_class": "SMALL"}
                         for r in self.conditioned["calls"]],
        }
        self.profile_file.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(set(read_profiles(self.profile_file)), set(self.ids))
        self.assertEqual(self.snapshot()["synthetic_call_count"], 140)

    def test_17_wrong_dimensions_are_rejected_as_no_compatible_berth(self):
        target = self.conditioned["calls"][0]
        target.update(cargo_type="LPG", dwt=120000, loa_m=290, draft_m=15)
        self.profile_file.write_text(json.dumps(self.conditioned), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "NO_COMPATIBLE_BERTH_IN_MODEL"):
            self.snapshot()
        report = audit(read_profiles(self.profile_file), self.mapping, self.facilities, self.ids)
        self.assertGreater(report["summary"]["blocked"], 0)
        self.assertEqual(report["blocked_calls"][0]["status"], "NO_COMPATIBLE_BERTH_IN_MODEL")

    def test_18_eligible_filter_only_pass_berths(self):
        snap = {"sk_physical": self.snapshot()}
        cid = self.ids[0]
        valid = snap["sk_physical"]["eligible_berths"][cid]
        self.assertEqual(eligible_berths(snap, cid, sorted(self.mapping)), valid)
        self.assertIsNone(assert_eligible(snap, cid, valid[0]))
        for invalid in set(self.mapping) - set(valid):
            with self.assertRaisesRegex(ValueError, "INCOMPATIBLE_BERTH"):
                assert_eligible(snap, cid, invalid)

    def test_19_missing_compatibility_list_fails_closed(self):
        physical = self.snapshot()
        physical["eligible_berths"][self.ids[0]] = []
        with self.assertRaisesRegex(ValueError, "NO_COMPATIBLE_BERTH_IN_MODEL"):
            eligible_berths({"sk_physical": physical}, self.ids[0], sorted(self.mapping))

    def test_20_sk7_sk8_ambiguous_cargo_remains_unknown(self):
        vessel = {"dwt": 40000, "loa_m": 200, "draft_m": 10, "cargo_type": "REFINED_PRODUCT"}
        for code in ("SK#7", "SK#8"):
            check = check_physical_compatibility(vessel, self.facilities[code])
            self.assertEqual(check["status"], "UNKNOWN")
            self.assertTrue(any(issue["code"] == "CARGO_SOURCE_AMBIGUOUS" for issue in check["issues"]))

    def test_21_sk7_sk8_crude_is_explicit(self):
        vessel = {"dwt": 40000, "loa_m": 200, "draft_m": 10, "cargo_type": "CRUDE"}
        for code in ("SK#7", "SK#8"):
            self.assertEqual(check_physical_compatibility(vessel, self.facilities[code])["status"], "PASS")

    def test_22_fingerprints_freeze_all_input_files(self):
        physical = self.snapshot()
        verify_sk_snapshot(physical)
        self.assertEqual(set(physical["source_files"]), {"mapping", "facilities", "profiles"})
        self.profile_file.write_text(self.profile_file.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "modified"):
            verify_sk_snapshot(physical)

    def test_23_no_unverified_operational_safety_claim(self):
        physical = self.snapshot()
        self.assertIn("NOT_OPERATIONAL_APPROVAL", physical["scope"])
        self.assertIn("BERTHING_WEATHER", physical["not_evaluated"])
        self.assertIn("TANK_PIPELINE_CAPACITY", physical["not_evaluated"])

    def test_24_wrong_db_berth_ids_blocked(self):
        berths = self.berths[1:] + [{"berth_id": "SOMETHING_ELSE", "is_active": True}]
        with patch.dict(os.environ, self.env):
            with self.assertRaisesRegex(ValueError, "8 existing DB berths"):
                build_sk_snapshot_for_experiment("sk-physical-v3-test", self.calls, berths)

    def test_25_invalid_max_attempts(self):
        with self.assertRaisesRegex(ValueError, "max_attempts"):
            generate(self.ids, mode="conditioned", max_attempts=0,
                     mapping=self.mapping, facilities=self.facilities)

    def test_26_full_preflight_blocks_future_incompatible_call(self):
        # The first call is compatible; an undisclosed later call is not.
        last = self.conditioned["calls"][-1]
        last.update(cargo_type="LPG", dwt=120000, loa_m=290, draft_m=15)
        self.profile_file.write_text(
            json.dumps(self.conditioned, ensure_ascii=False), encoding="utf-8"
        )
        with patch.dict(os.environ, self.env):
            active = build_sk_snapshot_for_experiment(
                "sk-physical-v3-test", self.calls[:1], self.berths
            )
            self.assertEqual(active["synthetic_call_count"], 1)
            with self.assertRaisesRegex(ValueError, "NO_COMPATIBLE_BERTH_IN_MODEL"):
                preflight_full_source_scenario(
                    "sk-physical-v3-test", self.ids, self.berths
                )

    def test_27_full_preflight_requires_exact_source_ids(self):
        with patch.dict(os.environ, self.env):
            with self.assertRaisesRegex(ValueError, "V3_FULL_SOURCE_PROFILE_MISMATCH"):
                preflight_full_source_scenario(
                    "sk-physical-v3-test", self.ids[:-1], self.berths
                )

    def test_28_full_preflight_is_noop_for_legacy_experiment(self):
        with patch.dict(os.environ, self.env):
            self.assertIsNone(preflight_full_source_scenario(
                "source-daywork-v2", self.ids, self.berths
            ))


if __name__ == "__main__":
    unittest.main()
