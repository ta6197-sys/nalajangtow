"""Physical second and weather-slot boundaries, independent of settlement."""
import unittest
from datetime import datetime, timedelta

from weather_optimizer import WeatherSeries

from src.daily_planning.risk.time_precision import (berth_overlap, remaining_work_kg,
    scaled_work_seconds, work_timeline, worked_seconds_until)


def weather_rows(start, count, *, blocked=()):
    rows = []
    for index in range(count):
        at = start + timedelta(minutes=30 * index)
        rows.append({'일시': at.isoformat(sep=' '), '풍속(m/s)': 9 if index in blocked else 0,
                     '유의파고(m)': 0, '최대파고(m)': 0, '평균파고(m)': 0})
    return WeatherSeries(rows)


class RiskPhysicalTest(unittest.TestCase):
    def test_multiplier_preserves_exact_seconds_without_work_rounding(self):
        self.assertEqual(scaled_work_seconds(101, '1.05'), 6363)
        self.assertEqual(scaled_work_seconds(120, '1.10'), 7920)
        self.assertEqual(scaled_work_seconds(101, '1.001'), 6066)
        self.assertEqual(scaled_work_seconds(1, '1.0083'), 60)
        self.assertEqual(scaled_work_seconds(1, '1.0084'), 61)
        self.assertEqual(scaled_work_seconds(2, '1.0125'), 122)

    def test_start_and_completion_can_fall_inside_weather_slot(self):
        start = datetime(2026, 11, 2, 10, 12, 17)
        weather = weather_rows(datetime(2026, 11, 2, 10), 3)
        result = work_timeline(start, 71, weather, daylight=False)
        self.assertEqual(result['work_start_at'], '2026-11-02T10:12:17')
        self.assertEqual(result['completion_at'], '2026-11-02T10:13:28')

    def test_weather_boundary_pauses_without_30_minute_start_snapping(self):
        start = datetime(2026, 11, 2, 10, 29, 45)
        weather = weather_rows(datetime(2026, 11, 2, 10), 3, blocked=(1,))
        result = work_timeline(start, 30, weather, daylight=False)
        self.assertEqual(result['work_intervals'], [
            ['2026-11-02T10:29:45', '2026-11-02T10:30:00'],
            ['2026-11-02T11:00:00', '2026-11-02T11:00:15']])
        self.assertEqual(result['weather_pause_intervals'], [
            ['2026-11-02T10:30:00', '2026-11-02T11:00:00']])

    def test_berth_occupancy_uses_exact_seconds_and_half_open_bounds(self):
        first = dict(berth_id='BERTH-01', work_start_at='2026-11-02T10:00:01',
                     completion_at='2026-11-02T10:30:01')
        second = dict(berth_id='BERTH-01', work_start_at='2026-11-02T10:30:00',
                      completion_at='2026-11-02T11:00:00')
        self.assertTrue(berth_overlap(first, second))
        second['work_start_at'] = '2026-11-02T10:30:01'
        self.assertFalse(berth_overlap(first, second))

    def test_kg_balance_conserves_every_source_kg_at_second_cut(self):
        start = datetime(2026, 11, 2, 10, 12, 17)
        timeline = work_timeline(start, 61, weather_rows(datetime(2026, 11, 2, 10), 3),
                                 daylight=False)
        worked = worked_seconds_until(timeline['work_intervals'], start + timedelta(seconds=30))
        remaining = remaining_work_kg(101, 61, worked)
        self.assertEqual(worked, 30)
        self.assertEqual(remaining + (101 - remaining), 101)
        self.assertEqual(remaining_work_kg(101, 61, 61), 0)

    def test_daylight_night_pause_keeps_exact_work_seconds(self):
        start = datetime(2026, 11, 2, 16, 0, 17)
        weather = weather_rows(datetime(2026, 11, 2, 16), 100)
        result = work_timeline(start, 3 * 3600, weather, daylight=True)
        self.assertTrue(result['night_pause_intervals'])
        self.assertFalse(result['weather_pause_intervals'])
        self.assertEqual(worked_seconds_until(result['work_intervals'],
                                              datetime.fromisoformat(result['completion_at'])),
                         3 * 3600)


if __name__ == '__main__':
    unittest.main()
