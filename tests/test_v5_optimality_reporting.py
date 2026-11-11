import unittest
from unittest.mock import patch
from tests.test_v5_cost_aware_candidates import CostAwareCandidatesTest
from src.daily_planning.integrated_v3.v5_candidates import CandidatePool
from src.daily_planning.integrated_v3.v5_physical import solve_berth_schedule


class OptimalityReportingTest(CostAwareCandidatesTest):
    def test_unsolved_expansion_has_no_old_bound(self):
        original=CandidatePool.expand_cost
        def partial(pool,*args):
            self.assertGreater(original(pool,*args),0)
            raise TimeoutError('after expansion')
        with patch.object(CandidatePool,'expand_cost',partial):r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1')
        self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID');self.assertEqual(r['optimization_status'],'FEASIBLE')
        self.assertEqual(r['final_solver_status'],'NOT_SOLVED');self.assertIsNone(r['best_objective_bound_cents']);self.assertIsNone(r['optimality_gap'])
        self.assertEqual(r['best_validated_incumbent']['solver_status'],'OPTIMAL');self.assertNotEqual(r['best_incumbent_domain_fingerprint'],r['candidate_domain_fingerprint'])
    def test_expanded_timeout_has_no_old_bound(self):
        calls=[]
        def later(*args,**kwargs):
            calls.append(1)
            return solve_berth_schedule(*args,**kwargs) if len(calls)==1 else {'status':'TIME_LIMIT_NO_INCUMBENT','issues':[]}
        with patch('src.daily_planning.integrated_v3.v5_physical.solve_berth_schedule',side_effect=later):r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1')
        self.assertEqual(r['optimization_status'],'FEASIBLE');self.assertEqual(r['final_solver_status'],'TIME_LIMIT_NO_INCUMBENT');self.assertIsNone(r['optimality_gap']);self.assertIsNone(r['best_objective_bound_cents'])
    def test_proven_final_domain_has_correct_bound(self):
        r=self.run_auto(candidate_improvement_mode='COST_AWARE_V1');self.assertTrue(r['final_domain_optimality_proven'])
        self.assertEqual(r['best_objective_bound_cents'],r['total_projected_demurrage_cents']);self.assertEqual(r['optimality_gap'],0)
