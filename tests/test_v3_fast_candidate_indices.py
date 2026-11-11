"""Equivalence checks: accelerated indices must preserve all original options."""
import random
import unittest
from datetime import datetime, timedelta, timezone

from src.daily_planning.integrated_input.validator import timestamp, tokens
from src.daily_planning.physical_scheduling.conflict_index import (
    build_conflict_index, iter_potential_pairs)
from src.daily_planning.physical_scheduling.release_index import ReleaseEventIndex

KST = timezone(timedelta(hours=9))
BASE = datetime(2026, 11, 3, tzinfo=KST)


def at(minutes):
    return (BASE + timedelta(minutes=minutes)).isoformat()


def original_releases(options, locked, policy, mode, base):
    dock = set(policy.get('dock_4142_berths', ()))
    sources = list(locked) if mode == 'LOCKED_ONLY_V1' else list(options) + list(locked)
    return sorted({r['switching_end_at'] for r in sources
                   if r['call_id'] != base['call_id'] and
                   (r['berth_id'] == base['berth_id'] or
                    {r['berth_id'], base['berth_id']} <= dock) and
                   timestamp(r['switching_end_at']) > timestamp(base['etb_at'])},
                  key=timestamp)


def original_pairs(snapshot, policy, rows):
    interacting = set()
    for group in snapshot['workbook_tables']['groups']['rows']:
        for a in tokens(group['berth_ids']):
            for b in tokens(group['berth_ids']):
                interacting.add((a, b))
    dock = policy.get('dock_4142_berths', [])
    for a in dock:
        for b in dock:
            interacting.add((a, b))
    def envelope(row):
        return timestamp(row['berthing_start_at']), max(
            timestamp(row['etu_at']), timestamp(row['switching_end_at']))
    expected = set()
    for i, a in enumerate(rows):
        for j in range(i + 1, len(rows)):
            b = rows[j]
            if a['call_id'] == b['call_id']:
                continue
            if a['berth_id'] != b['berth_id'] and (a['berth_id'], b['berth_id']) not in interacting:
                continue
            x, y = envelope(a), envelope(b)
            if max(x[0], y[0]) < min(x[1], y[1]):
                expected.add((i, j))
    return expected


class FastCandidateIndexEquivalenceTest(unittest.TestCase):
    def setUp(self):
        self.random = random.Random(42)
        self.berths = ['B' + str(i) for i in range(1, 9)]
        self.policy = {'dock_4142_berths': ['B7', 'B8']}
        self.snapshot = {'workbook_tables': {'groups': {'rows': [
            {'rule_type': 'SAME_CARGO_NOT_CONCURRENT', 'berth_ids': 'B2;B3;B4'},
            {'rule_type': 'PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP', 'berth_ids': 'B5;B6'}
        ]}}}

    def rows(self, count):
        result = []
        for _ in range(count):
            start = self.random.randrange(0, 900)
            end = start + self.random.randrange(1, 150)
            result.append({
                'call_id': 'C' + str(self.random.randrange(max(1, count // 5))),
                'berth_id': self.random.choice(self.berths),
                'berthing_start_at': at(start),
                'etb_at': at(start + 1),
                'etu_at': at(end),
                'switching_end_at': at(end + self.random.randrange(0, 100))
            })
        return result

    def test_sweep_matches_original_pair_domain(self):
        for count in (0, 1, 5, 60, 400):
            rows = self.rows(count)
            expected = original_pairs(self.snapshot, self.policy, rows)
            predicate = build_conflict_index(self.snapshot, self.policy, rows)
            direct = {(i, j) for i in range(count) for j in range(i + 1, count)
                      if predicate(i, j)}
            emitted = list(iter_potential_pairs(self.snapshot, self.policy, rows))
            self.assertEqual(expected, direct)
            self.assertEqual(expected, set(emitted))
            self.assertEqual(len(emitted), len(set(emitted)))

    def test_release_events_identical_for_both_policies(self):
        options, locked = self.rows(250), self.rows(11)
        for mode in ('LOCKED_ONLY_V1', 'ALL_OPTIONS_V1'):
            index = ReleaseEventIndex(options, locked, self.policy, mode)
            for option in options[:50]:
                self.assertEqual(list(index.later_releases(option)),
                                 original_releases(options, locked, self.policy, mode, option))

    def test_release_cannot_be_self_generated(self):
        options = [
            {'call_id': 'A', 'berth_id': 'B7', 'etb_at': at(10), 'switching_end_at': at(60)},
            {'call_id': 'A', 'berth_id': 'B8', 'etb_at': at(20), 'switching_end_at': at(60)}
        ]
        self.assertEqual(list(ReleaseEventIndex(
            options, [], self.policy, 'ALL_OPTIONS_V1').later_releases(options[0])), [])
        options.append({'call_id': 'B', 'berth_id': 'B8', 'etb_at': at(5),
                        'switching_end_at': at(60)})
        self.assertEqual(list(ReleaseEventIndex(
            options, [], self.policy, 'ALL_OPTIONS_V1').later_releases(options[0])), [at(60)])

    def test_half_open_boundaries_and_same_vessel(self):
        rows = [
            dict(call_id='A', berth_id='B1', berthing_start_at=at(0),
                 etb_at=at(1), etu_at=at(60), switching_end_at=at(60)),
            dict(call_id='B', berth_id='B1', berthing_start_at=at(60),
                 etb_at=at(61), etu_at=at(90), switching_end_at=at(90)),
            dict(call_id='A', berth_id='B1', berthing_start_at=at(30),
                 etb_at=at(31), etu_at=at(70), switching_end_at=at(70))
        ]
        self.assertEqual(set(iter_potential_pairs(self.snapshot, self.policy, rows)), {(1, 2)})

    def test_deadline_checked_during_sweep(self):
        calls = []
        list(iter_potential_pairs(self.snapshot, self.policy, self.rows(110),
                                  check_deadline=lambda: calls.append(1)))
        self.assertGreater(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
