"""Physical second and weather-slot boundaries, independent of settlement."""
import unittest
from datetime import datetime, timedelta

from weather_optimizer import WeatherSeries

from src.daily_planning.risk.physical import berth_overlap, scaled_work_seconds, work_timeline


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
        with self.assertRaisesRegex(ValueError, 'whole seconds'):
            scaled_work_seconds(101, '1.001')

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


if __name__ == '__main__':
    unittest.main()
