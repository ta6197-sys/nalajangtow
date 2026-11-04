"""Stage 4 pure timing contracts: no DB writes or CP-SAT behavior change."""
import unittest

from src.daily_planning.berth_phases import build_phase_timeline, occupancy_overlap


class BerthPhaseTests(unittest.TestCase):
    def row(self, call_id="DEM-0001", berth="BERTH-06",
            start="2026-11-04T09:00:00+09:00", end="2026-11-04T12:00:00+09:00"):
        return {"call_id": call_id, "berth_id": berth,
                "work_start_at": start, "completion_at": end}

    def test_zero_durations_preserve_legacy_occupancy(self):
        timeline = build_phase_timeline(self.row())
        self.assertEqual(timeline["berthing_start_at"], "2026-11-04T09:00:00+09:00")
        self.assertEqual(timeline["cargo_work_start_at"], "2026-11-04T09:00:00+09:00")
        self.assertEqual(timeline["berth_release_at"], "2026-11-04T12:00:00+09:00")
        self.assertEqual(timeline["berth_occupied_minutes"], 180)

    def test_nonzero_phase_durations_expand_occupancy_only(self):
        timeline = build_phase_timeline(
            self.row(start="2026-11-04T10:00:00+09:00"),
            berthing_start_at="2026-11-04T09:00:00+09:00",
            berthing_minutes=60, unberthing_minutes=30,
            available_at="2026-11-04T08:00:00+09:00")
        self.assertEqual(timeline["cargo_work_start_at"], "2026-11-04T10:00:00+09:00")
        self.assertEqual(timeline["cargo_work_end_at"], "2026-11-04T12:00:00+09:00")
        self.assertEqual(timeline["berth_release_at"], "2026-11-04T12:30:00+09:00")
        self.assertEqual(timeline["berth_occupied_minutes"], 210)

    def test_phase_occupancy_overlap_while_cargo_does_not(self):
        first = build_phase_timeline(
            self.row(end="2026-11-04T12:00:00+09:00"),
            unberthing_minutes=60)
        second = build_phase_timeline(
            self.row("DEM-0002", start="2026-11-04T12:30:00+09:00",
                     end="2026-11-04T14:00:00+09:00"))
        self.assertTrue(occupancy_overlap(first, second))

    def test_different_berths_do_not_overlap(self):
        a = build_phase_timeline(self.row())
        b = build_phase_timeline(self.row("DEM-0002", berth="BERTH-07"))
        self.assertFalse(occupancy_overlap(a, b))

    def test_meeting_boundary_does_not_overlap(self):
        a = build_phase_timeline(self.row())
        b = build_phase_timeline(self.row("DEM-0002",
            start="2026-11-04T12:00:00+09:00",
            end="2026-11-04T13:00:00+09:00"))
        self.assertFalse(occupancy_overlap(a, b))

    def test_misaligned_berthing_start_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly after"):
            build_phase_timeline(self.row(), berthing_minutes=60,
                berthing_start_at="2026-11-04T07:30:00+09:00")

    def test_pre_availability_berthing_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "before vessel availability"):
            build_phase_timeline(self.row(), available_at="2026-11-04T09:30:00+09:00")

    def test_missing_explicit_start_for_nonzero_berthing_rejected(self):
        with self.assertRaisesRegex(ValueError, "Explicit berthing_start_at"):
            build_phase_timeline(self.row(), berthing_minutes=30)

    def test_non_grid_duration_is_rejected(self):
        for bad in (1, 15, 45, -30, True, 30.0, "30"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "multiple of 30"):
                    build_phase_timeline(self.row(), unberthing_minutes=bad)

    def test_naive_datetime_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            build_phase_timeline(self.row(start="2026-11-04T09:00:00"))

    def test_invalid_reverse_cargo_interval_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Cargo completion"):
            build_phase_timeline(self.row(start="2026-11-04T13:00:00+09:00",
                                          end="2026-11-04T12:00:00+09:00"))


if __name__ == "__main__":
    unittest.main()
