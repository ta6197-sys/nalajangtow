import unittest

from src.daily_planning.risk.metrics import risk_statistics, compare_scenario_costs


class RiskStatisticsTest(unittest.TestCase):
    def test_equal_weight_example(self):
        r = risk_statistics([0, 10, 20, 100])
        self.assertEqual(r['expected_cost_cents'], '32.500000')
        self.assertEqual(r['var_cents'], 100)
        self.assertEqual(r['cvar_cents'], '100.000000')

    def test_weighted_cvar_boundary(self):
        r = risk_statistics([0, 100, 200], ['0.93', '0.04', '0.03'])
        self.assertEqual(r['var_cents'], 100)
        self.assertEqual(r['cvar_cents'], '160.000000')
        self.assertEqual(r['expected_cost_cents'], '10.000000')

    def test_mass_straddles_var(self):
        r = risk_statistics([0, 100], ['0.96', '0.04'])
        self.assertEqual(r['var_cents'], 0)
        self.assertEqual(r['cvar_cents'], '80.000000')

    def test_negative_paired_deltas_are_legal(self):
        r = risk_statistics([-100, 0, 20, 40])
        self.assertEqual(r['var_cents'], 40)
        self.assertEqual(r['cvar_cents'], '40.000000')

    def test_bad_weights_and_types(self):
        for weights in (['0.3', '0.3'], ['-0.1', '1.1']):
            with self.assertRaises(ValueError):
                risk_statistics([10, 20], weights)
        for costs in ([10.5, 20], [True, 2], []):
            with self.assertRaises(ValueError):
                risk_statistics(costs)
        with self.assertRaises(ValueError):
            risk_statistics([1, 2], alpha=1)

    def test_common_scenarios_and_win_probability(self):
        results = compare_scenario_costs({
            'A': {'s1': 100, 's2': 200, 's3': 300},
            'B': {'s1': 90, 's2': 200, 's3': 350},
        }, weights_by_scenario={'s1': '0.5', 's2': '0.3', 's3': '0.2'})
        b = results['risk_by_candidate']['B']
        self.assertEqual(b['probability_cheaper_than_baseline'], '0.500000')
        self.assertEqual(b['probability_equal_to_baseline'], '0.300000')
        self.assertEqual(b['expected_delta_vs_baseline_cents'], '5.000000')
        self.assertEqual(b['paired_delta_cvar_cents'], '50.000000')

    def test_missing_or_invalid_common_scenario_fails(self):
        with self.assertRaises(ValueError):
            compare_scenario_costs({'A': {'s1': 100}, 'B': {'s2': 120}})
        with self.assertRaises(ValueError):
            compare_scenario_costs({'A': {'s1': -1}})


if __name__ == '__main__':
    unittest.main()
