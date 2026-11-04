"""Self-contained physical checks; no DB, OR-Tools, network, or weather files."""
import unittest
from src.daily_planning.berth_rules import (
    check_physical_compatibility, check_call_against_virtual_berth,
    load_facilities, validate_mapping)

class SKBerthRulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facilities = load_facilities()

    def check(self, code, **kwargs):
        return check_physical_compatibility(kwargs, self.facilities[code])

    def test_sk_buoy_2_boundary_allowed(self):
        r=self.check("SKB#2",draft_m=21.21,dwt=80000,cargo_type="원유")
        self.assertEqual(r["status"],"PASS")
        self.assertIn("BERTHING_WEATHER",r["not_evaluated"])

    def test_sk_buoy_2_draft_exceeded(self):
        r=self.check("SKB#2",draft_m=21.22,dwt=100000,cargo_type="원유")
        self.assertEqual(r["status"],"FAIL")
        self.assertIn("MAX_DRAFT_VIOLATED",[x["code"] for x in r["issues"]])

    def test_sk_buoy_2_min_dwt(self):
        r=self.check("SKB#2",draft_m=20,dwt=79999,cargo_type="CRUDE")
        self.assertEqual(r["status"],"FAIL")

    def test_sk_buoy_2_max_dwt(self):
        r=self.check("SKB#2",draft_m=20,dwt=325001,cargo_type="CRUDE")
        self.assertEqual(r["status"],"FAIL")

    def test_sk_berth_4_1_min_loa_uses_more_conservative_sk_value(self):
        self.assertEqual(self.facilities["SK#4-1"]["min_loa_m"],90)
        r=self.check("SK#4-1",draft_m=8,dwt=10000,loa_m=89,cargo_type="석유제품")
        self.assertEqual(r["status"],"FAIL")

    def test_sk_berth_4_1_loa_edge(self):
        r=self.check("SK#4-1",draft_m=8,dwt=10000,loa_m=90,cargo_type="석유제품")
        self.assertEqual(r["status"],"PASS")

    def test_cargo_unlisted_when_source_unambiguous(self):
        r=self.check("SKB#2",draft_m=20,dwt=100000,cargo_type="LPG")
        self.assertEqual(r["status"],"FAIL")

    def test_missing_dimension_unknown(self):
        r=self.check("SKB#2",dwt=100000,cargo_type="원유")
        self.assertEqual(r["status"],"UNKNOWN")

    def test_unknown_source_cargo_not_guessed(self):
        # The workbook says 석유저제품 for SK#7. Do not silently fix it.
        r=self.check("SK#7",draft_m=12,dwt=100000,loa_m=200,cargo_type="석유제품")
        self.assertEqual(r["status"],"UNKNOWN")
        self.assertIn("CARGO_SOURCE_AMBIGUOUS",[x["code"] for x in r["issues"]])

    def test_sk7_crude_explicitly_allowed(self):
        r=self.check("SK#7",draft_m=12,dwt=100000,loa_m=200,cargo_type="원유")
        self.assertEqual(r["status"],"PASS")

    def test_mapping_all_unknown_is_not_accepted(self):
        mapping={f"BERTH-{i:02d}":None for i in range(1,9)}
        self.assertTrue(validate_mapping(mapping,self.facilities))

    def test_mapping_unique_valid(self):
        codes=list(self.facilities)[:8]
        mapping={f"BERTH-{i:02d}":codes[i-1] for i in range(1,9)}
        self.assertEqual(validate_mapping(mapping,self.facilities),[])

    def test_mapping_duplicate_invalid(self):
        mapping={f"BERTH-{i:02d}":"SKB#2" for i in range(1,9)}
        self.assertTrue(any("more than one" in x for x in validate_mapping(mapping,self.facilities)))

    def test_non_finite_input_rejected(self):
        with self.assertRaises(ValueError):
            self.check("SKB#2",draft_m="NaN",dwt=100000,cargo_type="원유")

    def test_unmapped_virtual_berth_unknown(self):
        r=check_call_against_virtual_berth({"draft_m":10},"BERTH-01",{},self.facilities)
        self.assertEqual(r["status"],"UNKNOWN")


if __name__ == "__main__":
    unittest.main()
