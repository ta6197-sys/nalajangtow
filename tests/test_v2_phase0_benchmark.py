import unittest
from benchmarks.v2_phase0 import fixture, run
from src.daily_planning.context import signature


class PhaseZeroBenchmarkTest(unittest.TestCase):
    def test_fixture_is_synthetic_and_hash_valid(self):
        snapshot, _ = fixture(4)
        self.assertEqual(snapshot['experiment_key'], 'SYNTHETIC_PHASE0')
        self.assertEqual(len(snapshot['calls']), 4)
        self.assertEqual(snapshot['snapshot_hash'], signature({k:v for k,v in snapshot.items() if k != 'snapshot_hash'}))

    def test_instrumented_replay_is_reproducible(self):
        one, two = run(2, 2), run(2, 2)
        self.assertEqual(one['candidates'], two['candidates'])
        self.assertGreater(one['timing']['replay_calls'], 0)
        self.assertGreater(one['timing']['solver_calls'], 0)
        self.assertGreaterEqual(one['timing']['replay_seconds'], 0)
        self.assertGreaterEqual(one['timing']['solver_seconds'], 0)

if __name__ == '__main__': unittest.main()
