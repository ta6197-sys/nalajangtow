"""Weather overlay onset, union and source-grid boundary tests."""
import unittest
from datetime import datetime, timedelta

from rolling_horizon import RULE
from weather_optimizer import price_contract

from src.daily_planning.risk.physical import work_timeline
from src.daily_planning.risk.weather import ScenarioWeather, merge_blocks, scenario_weather
from .test_risk_physical import weather_rows
from .test_risk_settlement_precision import contract


class RiskWeatherTest(unittest.TestCase):
    def test_overlapping_blocks_merge_before_replay(self):
        start = datetime(2026, 11, 2, 10)
        blocks = merge_blocks([(start, start + timedelta(minutes=30)),
                               (start + timedelta(minutes=15), start + timedelta(minutes=45))])
        self.assertEqual(blocks, [(start, start + timedelta(minutes=45))])

    def test_overlay_and_source_weather_form_one_pause(self):
        start = datetime(2026, 11, 2, 10)
        source = weather_rows(start, 5, blocked=(1,))
        weather = ScenarioWeather(source, [(start + timedelta(minutes=15),
                                            start + timedelta(minutes=45))])
        self.assertFalse(weather.blocked(start + timedelta(minutes=14, seconds=59), RULE))
        self.assertTrue(weather.blocked(start + timedelta(minutes=15), RULE))
        self.assertTrue(weather.blocked(start + timedelta(minutes=59), RULE))
        self.assertFalse(weather.blocked(start + timedelta(hours=1), RULE))
        result = work_timeline(start, 3600, weather, daylight=False)
        self.assertEqual(result['weather_pause_intervals'], [
            ['2026-11-02T10:15:00', '2026-11-02T11:00:00']])
        self.assertEqual(result['completion_at'], '2026-11-02T11:45:00')

    def test_scenario_onset_can_split_weather_source_slot_at_second(self):
        start = datetime(2026, 11, 2, 10)
        source = weather_rows(start, 4)
        weather = scenario_weather(source, dict(additional_weather_block_minutes=30,
            weather_block_onset_offset_minutes=15,
            weather_block_onset_offset_seconds=17), start)
        result = work_timeline(start, 60, weather, daylight=False)
        self.assertEqual(result['completion_at'], '2026-11-02T10:01:00')
        longer = work_timeline(start, 1800, weather, daylight=False)
        self.assertEqual(longer['weather_pause_intervals'], [
            ['2026-11-02T10:15:17', '2026-11-02T10:45:17']])

    def test_weather_laytime_policy_changes_price_without_double_count(self):
        start = datetime(2026, 11, 2, 10)
        source = weather_rows(start, 5)
        weather = ScenarioWeather(source, [(start + timedelta(minutes=30),
                                            start + timedelta(minutes=90))])
        timeline = work_timeline(start, 3600, weather, daylight=False)
        finish = datetime.fromisoformat(timeline['completion_at'])
        pauses = [(datetime.fromisoformat(a), datetime.fromisoformat(b))
                  for a, b in timeline['weather_pause_intervals']]
        costs = {}
        for policy in ('EXCLUDE', 'HALF', 'COUNT'):
            priced = price_contract(contract(allowed_minutes=60,
                                             daily_demurrage_usd='1440',
                                             weather_policy=policy),
                                    start, finish, pauses, weather, RULE)
            costs[policy] = priced['demurrage_cents']
        self.assertEqual(costs, {'EXCLUDE': 0, 'HALF': 3000, 'COUNT': 6000})


if __name__ == '__main__':
    unittest.main()
