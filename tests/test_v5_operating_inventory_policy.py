"""Stage 2A exact physical bounds and warning-only inventory behavior."""
import unittest
import copy
from fractions import Fraction

from src.daily_planning.integrated_v3.v5_inventory_policy import (
    POLICY_VERSION, physical_bounds, operating_bounds, operating_warning_summary, policy_view,
)
from src.daily_planning.tank.v5_inventory import validate_v5_inventory
from tests.test_v5_tank_inventory import fixture, at, START


def balanced_fixture(flow='0', initial='500'):
    snapshot = fixture()
    snapshot['scenario_id'] = 'V5_BALANCED_154'
    tank = snapshot['tanks'][0]
    tank.update(initial_inventory_bbl=initial, min_inventory_bbl='100',
                effective_max_inventory_bbl='950',
                inventory_policy_version=POLICY_VERSION,
                operational_min_inventory_bbl='100',
                operational_max_inventory_bbl='950')
    snapshot['fixed_process_flows']['segments'][0]['fixed_net_flow_bbl_per_hour'] = flow
    return snapshot


class InventoryPolicyStage2ATest(unittest.TestCase):
    def replay(self, s, **kw):
        return validate_v5_inventory(s, [], start_at=kw.pop('start_at', START),
                                     end_at=kw.pop('end_at', at(4)), **kw)

    def test_exact_physical_vs_operating_bounds(self):
        tank = balanced_fixture()['tanks'][0]
        self.assertEqual(physical_bounds(tank), (Fraction(0), Fraction(1000)))
        self.assertEqual(operating_bounds(tank), (Fraction(100), Fraction(950)))

    def test_warning_range_does_not_invalidate_below_minimum(self):
        result = self.replay(balanced_fixture(flow='-105'))  # 500 -> 80
        self.assertEqual(result['status'], 'VALID', result['issues'])
        self.assertIn('OPERATIONAL_MINIMUM_BREACH',
                      {w['code'] for w in result['operational_warnings']})
        self.assertEqual(result['end_state']['inventory_bbl']['T'], '80')

    def test_warning_range_does_not_invalidate_above_maximum(self):
        result = self.replay(balanced_fixture(flow='120'))  # 500 -> 980
        self.assertEqual(result['status'], 'VALID', result['issues'])
        self.assertIn('OPERATIONAL_MAXIMUM_BREACH',
                      {w['code'] for w in result['operational_warnings']})

    def test_exact_zero_and_exact_capacity_are_valid(self):
        for amount in ('0', '1000'):
            with self.subTest(amount=amount):
                result = self.replay(balanced_fixture(initial=amount))
                self.assertEqual(result['status'], 'VALID', result['issues'])

    def test_operating_boundaries_do_not_warn(self):
        for amount in ('100', '950'):
            with self.subTest(amount=amount):
                result = self.replay(balanced_fixture(initial=amount))
                self.assertEqual(result['status'], 'VALID')
                self.assertEqual(result['operational_warnings'], [])

    def test_below_zero_and_over_capacity_are_invalid(self):
        for amount in ('-1', '1001'):
            with self.subTest(amount=amount):
                result = self.replay(balanced_fixture(initial=amount))
                self.assertNotEqual(result['status'], 'VALID')
                self.assertIn('INVENTORY_OUT_OF_BOUNDS',
                              {x['code'] for x in result['issues']})

    def test_physical_flow_violation_is_hard_failure(self):
        for flow in ('-200', '200'):
            with self.subTest(flow=flow):
                result = self.replay(balanced_fixture(flow=flow))
                self.assertEqual(result['status'], 'INVALID')
                self.assertIn('INVENTORY_OUT_OF_BOUNDS', {i['code'] for i in result['issues']})

    def test_warning_range_carry_in_survives_next_rolling_interval(self):
        snapshot = balanced_fixture()
        result = self.replay(snapshot, start_at=at(1), end_at=at(2), initial_inventory={'T': '80'})
        self.assertEqual(result['status'], 'VALID', result['issues'])
        self.assertTrue(result['operational_warnings'])

    def test_legacy_v5_without_policy_preserves_source_bounds(self):
        legacy = fixture()['tanks'][0]
        self.assertEqual(physical_bounds(legacy), (Fraction(0), Fraction(1000)))

    def test_exact_confirmed_10m_barrel_boundaries(self):
        cases = [
            ('0', True), ('800000', True), ('1000000', False),
            ('9500000', False), ('9700000', True), ('10000000', True),
        ]
        for amount, expect_warning in cases:
            with self.subTest(inventory_bbl=amount):
                s = balanced_fixture(initial=amount)
                t = s['tanks'][0]
                t.update(capacity_bbl='10000000', min_inventory_bbl='1000000',
                         effective_max_inventory_bbl='9500000',
                         operational_min_inventory_bbl='1000000',
                         operational_max_inventory_bbl='9500000')
                r = self.replay(s)
                self.assertEqual(r['status'], 'VALID', r['issues'])
                self.assertEqual(bool(r['operational_warnings']), expect_warning)
        for amount in ('-1', '10000001'):
            with self.subTest(hard_invalid=amount):
                s = balanced_fixture(initial=amount)
                t = s['tanks'][0]
                t.update(capacity_bbl='10000000', min_inventory_bbl='1000000',
                         effective_max_inventory_bbl='9500000',
                         operational_min_inventory_bbl='1000000',
                         operational_max_inventory_bbl='9500000')
                r = self.replay(s)
                self.assertEqual(r['status'], 'INVALID')
                self.assertIn('INVENTORY_OUT_OF_BOUNDS',
                              {x['code'] for x in r['issues']})

    def test_cp_sat_enforces_only_physical_bounds(self):
        from ortools.sat.python import cp_model
        from src.daily_planning.integrated_v3.v5_inventory_constraints import add_inventory_constraints
        tank = balanced_fixture()['tanks'][0]
        def solve(external):
            model = cp_model.CpModel()
            prepared = {
                'tanks': {'T': {'events': [0, 1],
                                'external_cumulative_bbl': [Fraction(0), Fraction(external)]}},
                'options': [],
            }
            add_inventory_constraints(model, [], prepared, [tank],
                                      initial_inventory={'T': '500'}, option_count=0)
            return cp_model.CpSolver().Solve(model)
        self.assertEqual(solve(-420), cp_model.OPTIMAL)  # Final 80: advisory only
        self.assertEqual(solve(-520), cp_model.INFEASIBLE)  # Final -20: impossible
        self.assertEqual(solve(480), cp_model.OPTIMAL)  # Final 980: advisory only
        self.assertEqual(solve(520), cp_model.INFEASIBLE)  # Final 1020: impossible

    def test_warning_summary_rejects_physical_violation(self):
        tank = balanced_fixture()['tanks'][0]
        with self.assertRaisesRegex(ValueError, 'Physical inventory violation'):
            operating_warning_summary([tank], {'T': [{'at': START, 'inventory_bbl': '-1'}]})

    def test_policy_view_preserves_source_and_legacy_bounds(self):
        from src.daily_planning.integrated_input.provenance import canonical, digest
        source=balanced_fixture()
        for key in ('inventory_policy_version','operational_min_inventory_bbl','operational_max_inventory_bbl'):
            source['tanks'][0].pop(key)
        before=copy.deepcopy(source)
        self.assertEqual(physical_bounds(policy_view(source,None)['tanks'][0]),(Fraction(100),Fraction(950)))
        self.assertEqual(physical_bounds(policy_view(source,POLICY_VERSION)['tanks'][0]),(Fraction(0),Fraction(1000)))
        validated=self.replay(policy_view(source,POLICY_VERSION))
        self.assertEqual(validated['source_fingerprint'],digest(canonical(source).encode()))
        self.assertEqual(source,before)

    def test_rolling_empty_window_carries_warning_inventory(self):
        from src.daily_planning.integrated_v3.v5_rolling import _empty_tank
        snapshot=balanced_fixture()
        first=copy.deepcopy(snapshot['fixed_process_flows']['segments'][0])
        first.update(end_at=at(24),fixed_net_flow_bbl_per_hour='-35/2')
        second=copy.deepcopy(first);second.update(start_at=at(24),end_at=at(48),fixed_net_flow_bbl_per_hour='0')
        snapshot['fixed_process_flows']['segments']=[first,second]
        a=_empty_tank(snapshot,START,{'inventory':None})
        self.assertEqual(a['carried_inventory_bbl']['T'],'80')
        self.assertEqual(a['operational_warnings'][0]['code'],'OPERATIONAL_MINIMUM_BREACH')
        b=_empty_tank(snapshot,at(24),{'inventory':a['carried_inventory_bbl']})
        self.assertEqual(b['carried_inventory_bbl']['T'],'80')
        self.assertTrue(b['operational_warnings'])


if __name__ == '__main__':
    unittest.main()
