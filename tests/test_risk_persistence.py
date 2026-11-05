"""Pure Risk record preparation; no live PostgreSQL connection required."""
import copy
import unittest
import uuid

from src.daily_planning.risk.persistence import prepare_analysis_records
from src.daily_planning.risk.scenarios import load_scenario_config
from src.daily_planning.risk.service import evaluate_monte_carlo
from .test_risk_arrival_replay import fixture


class RiskPersistenceTest(unittest.TestCase):
    def report(self):
        snapshot, candidate = fixture()
        run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'risk-persistence-test'))
        candidates = [dict(candidate, label=label,
            candidate_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'risk-test-'+label)))
            for label in 'ABC']
        config = load_scenario_config('config/risk_synthetic_v1.json')
        config['sample_count'] = 3
        report = evaluate_monte_carlo(snapshot, candidates, config)
        report['run_id'] = run_id
        report['scenario_config'] = config
        report['candidate_refs'] = [dict(label=label,
            candidate_id=candidate['candidate_id'],
            schedule_signature='schedule-'+label,
            freeze_signature='freeze-'+label)
            for label, candidate in zip('ABC', candidates)]
        return report

    def test_records_are_deterministic_and_cover_every_outcome(self):
        report = self.report()
        first = prepare_analysis_records(report)
        second = prepare_analysis_records(report)
        self.assertEqual(first, second)
        self.assertEqual(len(first['scenarios']), 3)
        self.assertEqual(len(first['outcomes']), 9)
        self.assertEqual(len(first['vessels']), 27)
        self.assertEqual(first['analysis']['summary']['scenario_count'], 3)
        self.assertTrue(all(row['content_hash'] for row in first['outcomes']))

    def test_fabricated_or_missing_cost_cannot_be_persisted(self):
        report = self.report()
        sid = report['scenarios'][0]['scenario_id']
        changed = copy.deepcopy(report)
        changed['scenario_results']['A'][sid]['cost_cents'] += 1
        with self.assertRaisesRegex(ValueError, 'reconcile'):
            prepare_analysis_records(changed)
        changed = copy.deepcopy(report)
        del changed['scenario_results']['B'][sid]
        with self.assertRaisesRegex(ValueError, 'Missing or extra'):
            prepare_analysis_records(changed)


if __name__ == '__main__':
    unittest.main()
