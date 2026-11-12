import unittest
from datetime import datetime
from src.daily_planning.physical_scheduling.v5_stable_choice import (
    MODE, MAX_GROUPS, canonical_row_key, schedule_key, option_ranks,
    stabilize_proven_cost,
)


def row(cid, etb, berth, hose='2026-11-12T15:00:00+09:00'):
    return {'call_id': cid, 'etb_at': etb, 'berth_id': berth,
            'hose_connection_start_at': hose}


class PureTieBreakTests(unittest.TestCase):
    def setUp(self):
        self.options = [
            row('A', '2026-11-12T14:00:00+09:00', 'BERTH-02'),
            row('A', '2026-11-12T12:00:00+09:00', 'BERTH-02'),
            row('B', '2026-11-12T14:00:00+09:00', 'BERTH-01'),
            row('A', '2026-11-12T12:00:00+09:00', 'BERTH-01'),
        ]

    def test_rank_earliest_then_berth(self):
        got = option_ranks(self.options, [[0,1,3],[2]])
        self.assertEqual(got[0], {3:0, 1:1, 0:2})
        self.assertEqual(got[1], {2:0})

    def test_order_independent_of_option_indices(self):
        f = [self.options[i] for i in [3,0,1,2]]
        ranks = option_ranks(f, [[0,1,2],[3]])
        self.assertEqual([f[i]['etb_at'] for i in sorted(ranks[0],key=ranks[0].get)],
                         [self.options[3]['etb_at'],self.options[1]['etb_at'],self.options[0]['etb_at']])

    def test_timezone_equivalence(self):
        a = row('A','2026-11-12T14:00:00+09:00','BERTH-01')
        b = row('A','2026-11-12T05:00:00+00:00','BERTH-01')
        self.assertEqual(canonical_row_key(a)[0],canonical_row_key(b)[0])

    def test_schedule_key_ignores_row_order(self):
        self.assertEqual(schedule_key([self.options[0],self.options[2]]),
                         schedule_key([self.options[2],self.options[0]]))

    def test_schedule_key_prefers_earlier(self):
        self.assertLess(schedule_key([self.options[3]]),schedule_key([self.options[0]]))

    def test_reject_naive_etb(self):
        with self.assertRaises(ValueError):
            canonical_row_key(row('A','2026-11-12T14:00:00','BERTH-01'))

    def test_scope_limit_is_s5_s10_only(self):
        self.assertEqual(MAX_GROUPS,12)


class StubCP:
    OPTIMAL = 4
    FEASIBLE = 2


class NoCallModel:
    def Add(self,*a,**k): raise AssertionError('Model must not change on fallback')


class FallbackTests(unittest.TestCase):
    def test_skip_unproven_primary(self):
        selection, status = stabilize_proven_cost(NoCallModel(),[],[],[[]],None,[],StubCP.FEASIBLE,0,float('inf'),StubCP)
        self.assertEqual(status['status'],'SKIPPED_PRIMARY_UNPROVEN')
        self.assertEqual(selection,[])

    def test_skip_oversized_scope(self):
        selection, status = stabilize_proven_cost(NoCallModel(),[],[],[[]]*13,None,[],StubCP.OPTIMAL,0,float('inf'),StubCP)
        self.assertEqual(status['status'],'SKIPPED_SCOPE_OVER_12_CALLS')

    def test_fails_invalid_primary_cost(self):
        with self.assertRaises(ValueError):
            stabilize_proven_cost(NoCallModel(),[],[],[[]],None,[],StubCP.OPTIMAL,3.5,float('inf'),StubCP)


if __name__=='__main__':
    unittest.main()

# Small exact in-memory CP-SAT stand-in: tests the helper's stage ordering and
# cost fence when OR-Tools is not installed in the packaging environment.
class Expr:
    def __init__(self, terms=None, const=0):
        self.terms = dict(terms or {})
        self.const = const
    def __add__(self, other):
        if isinstance(other,int):other=Expr(const=other)
        d = dict(self.terms)
        for k,v in other.terms.items():d[k] = d.get(k,0)+v
        return Expr(d,self.const+other.const)
    __radd__ = __add__
    def __eq__(self, other):
        return (self,other)
    def value(self, chosen):
        return self.const + sum(v*int(k in chosen) for k,v in self.terms.items())

class Var:
    def __init__(self,index):self.index=index
    def __rmul__(self,number):return Expr({self.index:number})

class FakeModel:
    def __init__(self,groups):self.constraints=[];self.groups=groups;self.objective=None
    def Add(self, constraint):self.constraints.append(constraint)
    def Minimize(self,expr):self.objective=expr

class FakeSolver:
    def __init__(self):self.parameters=type('Params',(),{})();self.chosen=None;self.objective=None
    def Solve(self,m):
        import itertools
        solutions=[]
        for pick in itertools.product(*m.groups):
            p=set(pick)
            if all(expr.value(p)==expected for expr,expected in m.constraints):
                solutions.append((m.objective.value(p),sorted(p)))
        if not solutions:return 0
        self.objective,self.chosen=min(solutions)
        return 4
    def Value(self,x):
        if isinstance(x,Var):return int(x.index in self.chosen)
        return x.value(set(self.chosen))
    def StatusName(self,status):return {4:'OPTIMAL',0:'INFEASIBLE'}.get(status,'UNKNOWN')

class FakeCP:
    OPTIMAL=4
    FEASIBLE=2
    CpSolver=FakeSolver

class LexicographicProofTests(unittest.TestCase):
    def test_equal_cost_choices_are_canonical_and_cost_fixed(self):
        options=[
            row('A','2026-11-12T14:00:00+09:00','BERTH-02'),
            row('A','2026-11-12T12:00:00+09:00','BERTH-01'),
            row('B','2026-11-12T18:00:00+09:00','BERTH-03'),
            row('B','2026-11-12T17:00:00+09:00','BERTH-04'),
        ]
        groups=[[0,1],[2,3]]
        vars_=[Var(i) for i in range(4)]
        model=FakeModel(groups)
        cost=10*vars_[0]+10*vars_[1]+5*vars_[2]+5*vars_[3]
        chosen,info=stabilize_proven_cost(model,vars_,options,groups,cost,[0,2],FakeCP.OPTIMAL,15,float('inf'),FakeCP)
        self.assertEqual(chosen,[1,3])
        self.assertEqual(info['status'],'PROVEN_CANONICAL_SELECTION')
        self.assertEqual(info['secondary_stages_proven'],2)
        self.assertEqual(cost.value(set(chosen)),15)

    def test_cost_fence_prevents_choosing_more_expensive_earlier_etb(self):
        options=[row('A','2026-11-12T12:00:00+09:00','BERTH-01'),
                 row('A','2026-11-12T13:00:00+09:00','BERTH-01')]
        groups=[[0,1]]
        vars_=[Var(0),Var(1)]
        model=FakeModel(groups)
        cost=11*vars_[0]+10*vars_[1]
        chosen,info=stabilize_proven_cost(model,vars_,options,groups,cost,[1],FakeCP.OPTIMAL,10,float('inf'),FakeCP)
        self.assertEqual(chosen,[1])
        self.assertEqual(info['status'],'PROVEN_CANONICAL_SELECTION')
