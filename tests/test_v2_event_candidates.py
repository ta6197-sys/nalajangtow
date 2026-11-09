import unittest
from collections import OrderedDict
from benchmarks.v2_phase0 import fixture
from src.daily_planning.event_candidates import EVENTS, EVENTS_WEATHER, event_anchors, weather_release_events
from src.daily_planning.exact_search import build_domain, exact_search
from rolling_horizon import RULE
from datetime import timedelta


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

    def test_warm_start_keeps_valid_candidate_and_bound(self):
        snapshot, weather = fixture(4)
        domain = build_domain(snapshot, weather, candidate_generator=EVENTS)
        legacy, legacy_record = exact_search(snapshot, weather, domain, time_limit=2)
        hinted, record = exact_search(snapshot, weather, domain, time_limit=2, warm_start=True)
        self.assertEqual(hinted['evaluation']['summary']['demurrage_increment_to_completion_cents'], legacy['evaluation']['summary']['demurrage_increment_to_completion_cents'])
        self.assertLessEqual(record['valid_lower_bound'], record['exact_objective'])
        self.assertIn('warm_start_hint_used', record)
        self.assertEqual(record['solver_status'], legacy_record['solver_status'])

    def test_hint_respects_excluded_schedule_and_cost_cap(self):
        snapshot, weather = fixture(2)
        domain = build_domain(snapshot, weather, candidate_generator=EVENTS)
        first, _ = exact_search(snapshot, weather, domain, time_limit=2, warm_start=True)
        cap = first['evaluation']['summary']['demurrage_increment_to_completion_cents']
        alternate, record = exact_search(snapshot, weather, domain, objective='wait', time_limit=2,
                                         cost_cap=cap, excluded_signatures=[first['schedule_signature']], warm_start=True)
        self.assertIsNotNone(alternate)
        self.assertNotEqual(alternate['schedule_signature'], first['schedule_signature'])
        self.assertLessEqual(alternate['evaluation']['summary']['demurrage_increment_to_completion_cents'], cap)
        self.assertLessEqual(record['valid_lower_bound'], record['exact_objective'])

    def test_rejected_cost_cap_cannot_produce_hint_or_candidate(self):
        snapshot, weather = fixture(2)
        domain = build_domain(snapshot, weather, candidate_generator=EVENTS)
        result, record = exact_search(snapshot, weather, domain, time_limit=2, cost_cap=0, warm_start=True)
        self.assertIsNone(result)
        self.assertFalse(record['warm_start_hint_used'])

    def test_weather_release_expands_domain_without_losing_legacy_options(self):
        snapshot, weather = fixture(2)
        at, source, _, waves = weather.rows[15]
        weather.rows[15] = (at, source, 9.0, waves)
        release = at + timedelta(minutes=30)
        self.assertIn(release, weather_release_events(weather, RULE, weather.first, weather.first + timedelta(hours=72)))
        legacy = build_domain(snapshot, weather)
        events = build_domain(snapshot, weather, candidate_generator=EVENTS_WEATHER)
        def keys(domain):
            return {(o['call_id'], o['row']['berth_id'], o['row']['work_start_at']) for o in domain['options'] if o['row']}
        self.assertLess(keys(legacy), keys(events))
        self.assertTrue(any(release.isoformat() == k[2][:19] for k in keys(events) - keys(legacy)))

    def test_unknown_version_rejected(self):
        snapshot,weather=fixture(2)
        with self.assertRaises(ValueError): build_domain(snapshot,weather,candidate_generator='unknown')

if __name__ == '__main__': unittest.main()
