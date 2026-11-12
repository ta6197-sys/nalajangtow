"""Real CP-SAT integration check, run in the user's existing Python 3.11 env."""
import unittest
from time import perf_counter
from src.daily_planning.physical_scheduling.v5_stable_choice import stabilize_proven_cost
try:
    from ortools.sat.python import cp_model
except ImportError:
    cp_model = None


@unittest.skipIf(cp_model is None, 'OR-Tools unavailable in packaging environment')
class RealORToolsTest(unittest.TestCase):
    def test_lexicographic_choice_preserves_exact_cost_and_resource_constraints(self):
        m = cp_model.CpModel()
        x = [m.NewBoolVar('x'+str(i)) for i in range(4)]
        groups = [[0,1],[2,3]]
        for group in groups:
            m.AddExactlyOne(x[i] for i in group)
        m.Add(x[1] + x[3] <= 1)  # Physical/tank-like cross-option conflict
        options = [
            {'call_id':'A','etb_at':'2026-11-12T14:00:00+09:00','berth_id':'BERTH-02'},
            {'call_id':'A','etb_at':'2026-11-12T12:00:00+09:00','berth_id':'BERTH-01'},
            {'call_id':'B','etb_at':'2026-11-12T18:00:00+09:00','berth_id':'BERTH-03'},
            {'call_id':'B','etb_at':'2026-11-12T17:00:00+09:00','berth_id':'BERTH-04'},
        ]
        cost = 10*x[0] + 10*x[1] + 5*x[2] + 5*x[3]
        m.Minimize(cost)
        primary = cp_model.CpSolver()
        primary.parameters.num_search_workers=1
        primary.parameters.random_seed=42
        status = primary.Solve(m)
        self.assertEqual(status,cp_model.OPTIMAL)
        first = [i for i in range(4) if primary.Value(x[i])]
        selected, evidence = stabilize_proven_cost(m,x,options,groups,cost,first,status,15,
                                                  perf_counter()+30,cp_model)
        self.assertEqual(evidence['status'],'PROVEN_CANONICAL_SELECTION')
        self.assertEqual(selected,[1,2])
        self.assertFalse(1 in selected and 3 in selected)
        self.assertEqual(sum([10,10,5,5][i] for i in selected),15)

if __name__=='__main__':unittest.main()
