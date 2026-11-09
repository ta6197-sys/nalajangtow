import unittest
from collections import OrderedDict
from benchmarks.v2_phase0 import fixture
from src.daily_planning.event_candidates import EVENTS, event_anchors
from src.daily_planning.exact_search import build_domain, exact_search


class EventCandidatesTest(unittest.TestCase):
    def test_legacy_choices_are_retained(self):
        snapshot, weather = fixture(4)
        legacy = build_domain(snapshot, weather)
        events = build_domain(snapshot, weather, candidate_generator=EVENTS)
        def keys(domain):
            return {(o['call_id'], o['row']['berth_id'], o['row']['work_start_at']) for o in domain['options'] if o['row']}
        self.assertLessEqual(keys(legacy), keys(events))
        self.assertEqual(len(keys(events)), sum(o['row'] is not None for o in events['options']))

    def test_actual_physical_release_is_anchor(self):
        call={'call_id':'a','available_at':'2026-11-01T00:00:00'}
        fixed=[{'work_start_at':'2026-11-01T01:00:00','completion_at':'2026-11-01T02:00:00','berthing_start_at':'2026-11-01T01:00:00','berth_release_at':'2026-11-01T03:00:00'}]
        anchors=event_anchors(call,fixed,{})
        self.assertIn('2026-11-01T03:00:00', {x.isoformat() for x in anchors})

    def test_cache_preserves_replayed_schedule_and_is_weather_scoped(self):
        snapshot, weather = fixture(2)
        domain = build_domain(snapshot, weather, candidate_generator=EVENTS)
        cache = OrderedDict()
        first, _ = exact_search(snapshot, weather, domain, time_limit=2, replay_cache=cache)
        old_keys = set(cache)
        second, _ = exact_search(snapshot, weather, domain, time_limit=2, replay_cache=cache)
        self.assertEqual(first['schedule_signature'], second['schedule_signature'])
        self.assertEqual(first['evaluation']['summary'], second['evaluation']['summary'])
        self.assertEqual(old_keys, set(cache))
        weather.rows[0] = (weather.rows[0][0], weather.rows[0][1], 0.1, weather.rows[0][3])
        exact_search(snapshot, weather, domain, time_limit=2, replay_cache=cache)
        self.assertGreater(len(cache), len(old_keys))

    def test_unknown_version_rejected(self):
        snapshot,weather=fixture(2)
        with self.assertRaises(ValueError): build_domain(snapshot,weather,candidate_generator='unknown')

if __name__ == '__main__': unittest.main()
