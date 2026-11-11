import unittest
from fractions import Fraction
from ortools.sat.python import cp_model
from src.daily_planning.integrated_v3.v5_inventory_constraints import scale_exact, InventoryScalingError, add_inventory_constraints


class InventoryConstraintArithmeticTest(unittest.TestCase):
    def test_exact_scaling_and_reduction(self):
        self.assertEqual(scale_exact([Fraction(1,2),Fraction(-3,4)],Fraction(-1,4),Fraction(5,4)),([2,-3],-1,5))
    def test_overflow_is_explicit(self):
        with self.assertRaises(InventoryScalingError):scale_exact([Fraction(1,2**63)],0,1)
    def test_fixed_and_optional_balance_and_gross_rates(self):
        model=cp_model.CpModel();x=model.NewBoolVar('x')
        tank={'tank_id':'T','min_inventory_bbl':'0','capacity_bbl':'10','max_receipt_rate_bbl_per_hour':'3','max_dispatch_rate_bbl_per_hour':'3'}
        def profile(amount,receipt,dispatch):return {'tank_id':'T','signed_cumulative_bbl':[Fraction(0),Fraction(amount)],'receipt_rates_bbl_per_hour':[Fraction(receipt)],'dispatch_rates_bbl_per_hour':[Fraction(dispatch)]}
        prepared={'tanks':{'T':{'events':[0,1],'external_cumulative_bbl':[Fraction(0),Fraction(0)]}},'options':[profile(2,2,0),profile(-1,0,1)]}
        stats=add_inventory_constraints(model,[x],prepared,[tank],initial_inventory={'T':'9'},option_count=1)
        model.Maximize(x);solver=cp_model.CpSolver();self.assertEqual(solver.Solve(model),cp_model.OPTIMAL);self.assertEqual(solver.Value(x),1)
        self.assertEqual(stats['inventory_constraints_count'],2);self.assertEqual(stats['throughput_constraints_count'],2)
