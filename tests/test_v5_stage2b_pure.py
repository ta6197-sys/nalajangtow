"""Standalone (stdlib only) tests for Stage 2B new modules.

In a cloned Docktimize repository the target modules install under src/...;
for this ZIP the tests load the included independent source directly.
"""
from __future__ import annotations
import unittest
from src.daily_planning.integrated_v3 import v5_inventory_risk as risk
from src.daily_planning.integrated_v3 import v5_deferral as admit
START='2026-11-03T00:00:00+09:00'
DAY='2026-11-04T00:00:00+09:00'
DAY2='2026-11-05T00:00:00+09:00'

class RiskBands(unittest.TestCase):
    def test_every_threshold(self):
        cases={0:'CRITICAL_LOW', 999999:'CRITICAL_LOW',1000000:'WARNING_LOW',
               1999999:'WARNING_LOW',2000000:'NORMAL',8000000:'NORMAL',
               8000001:'WARNING_HIGH',9000000:'WARNING_HIGH',
               9000001:'CRITICAL_HIGH',10000000:'CRITICAL_HIGH',
               -1:'PHYSICAL_INVALID',10000001:'PHYSICAL_INVALID'}
        for amount,code in cases.items():
            with self.subTest(amount=amount):
                r=risk.classify(str(amount),'10000000')
                self.assertEqual(r['code'],code)
                self.assertEqual(r['physical_valid'],0<=amount<=10000000)

    def test_exact_rational_boundary(self):
        self.assertEqual(risk.classify('3/10','3')['code'],'WARNING_LOW')
        self.assertEqual(risk.classify('12/5','3')['code'],'NORMAL')
        self.assertEqual(risk.classify('27/10','3')['code'],'WARNING_HIGH')

    def test_event_samples_and_no_duration_claim(self):
        series={'TANK-01':[{'at':START,'inventory_bbl':'5000000'},
                           {'at':DAY,'inventory_bbl':'8500000'},
                           {'at':DAY2,'inventory_bbl':'9500000'}]}
        r=risk.classify_inventory_points([{'tank_id':'TANK-01','capacity_bbl':'10000000'}],series)
        self.assertEqual([x['code'] for x in r['severity_transitions']],
                         ['NORMAL','WARNING_HIGH','CRITICAL_HIGH'])
        self.assertEqual(r['duration'],'NOT_DETERMINED')
        self.assertFalse(r['risk_affects_objective'])

    def test_physical_invalid_not_downgraded(self):
        with self.assertRaisesRegex(ValueError,'Physical stock'):
            risk.classify_inventory_points([{'tank_id':'T','capacity_bbl':'10000000'}],
                {'T':[{'at':START,'inventory_bbl':'-1'}]})


def vessel(cid,eta=START,fixed=False):
    return {'call_id':cid,'eta_at':eta,'fixed_etb_is_hard':fixed}

def failed(reason='INFEASIBLE',status='INFEASIBLE',issues=None):
    return {'status':'WINDOW_NOT_VALIDATED','reason':reason,
            'optimizer_result':{'solver_status':status,'issues':issues or []},'issues':issues or []}

class DeferralAdmission(unittest.TestCase):
    def setUp(self):
        self.calls={'A':vessel('A'),'B':vessel('B',DAY),
                    'C':vessel('C',DAY2),'FIX':vessel('FIX',START,True)}

    def test_full_scope_first_no_deferral(self):
        visited=[]
        def opt(ids,budget):
            visited.append(ids)
            return {'status':'VALIDATED'}
        r=admit.choose_admission(self.calls,self.calls,at=START,optimize=opt,total_budget_seconds=30)
        self.assertEqual(r['status'],'VALIDATED')
        self.assertEqual(r['deferred'],[])
        self.assertEqual(visited,[['A','B','C','FIX']])

    def test_safe_drop_newest_retains_fixed_and_oldest(self):
        visited=[]
        def opt(ids,budget):
            visited.append(ids)
            return failed() if 'C' in ids else {'status':'VALIDATED'}
        r=admit.choose_admission(self.calls,self.calls,at=START,optimize=opt,total_budget_seconds=30)
        self.assertEqual(r['status'],'VALIDATED')
        self.assertEqual(r['deferred'],['C'])
        self.assertEqual(r['admitted'],['A','B','FIX'])
        self.assertEqual(len(visited),2)
        self.assertEqual(r['deferred_cost'],'UNPRICED_LIABILITY_NOT_ZERO')

    def test_never_drop_prior_scheduled_or_protected(self):
        attempts=[]
        def opt(ids,budget):
            attempts.append(ids)
            return failed() if 'B' in ids else {'status':'VALIDATED'}
        r=admit.choose_admission(self.calls,self.calls,at=START,
           previous_schedule={'B'},protected={'FIX'},optimize=opt,total_budget_seconds=30)
        self.assertEqual(r['status'],'BLOCKED')
        self.assertTrue(all('B' in ids and 'FIX' in ids for ids in attempts))

    def test_unknown_policy_timeout_not_deferred(self):
        for condition in (failed('TIME_LIMIT_NO_INCUMBENT','UNKNOWN'),
                          failed('INFEASIBLE','INFEASIBLE',[{'code':'UNSUPPORTED_OPERATIONAL_POLICY'}]),
                          failed('INFEASIBLE','INFEASIBLE',[{'code':'NO_FEASIBLE_GENERATED_OPTION'}])):
            attempts=[]
            def opt(ids,budget):
                attempts.append(ids);return condition
            r=admit.choose_admission(self.calls,self.calls,at=START,optimize=opt,total_budget_seconds=30)
            self.assertEqual(r['status'],'BLOCKED')
            self.assertEqual(len(attempts),1)

    def test_history_warning_48h_and_2_deferrals(self):
        first=admit.advance_history(self.calls,{},at=START,due=['A','C'],
                 admitted=['C'],deferred=['A'])
        second=admit.advance_history(self.calls,first,at=DAY2,due=['A','C'],
                 admitted=['C'],deferred=['A'])
        self.assertEqual(second['A']['carryover_count'],2)
        self.assertEqual(second['A']['warnings'],['WAIT_AT_LEAST_48H','CARRYOVER_AT_LEAST_2'])
        self.assertTrue(admit.verify_history(self.calls,first,second,at=DAY2,due=['A','C'],
                   admitted=['C'],deferred=['A'],protected=()))
        corrupted=dict(second);corrupted['A']=dict(corrupted['A'],carryover_count=4)
        with self.assertRaises(ValueError):
            admit.verify_history(self.calls,first,corrupted,at=DAY2,due=['A','C'],
                   admitted=['C'],deferred=['A'],protected=())

    def test_fixed_etb_cannot_be_deferred(self):
        with self.assertRaisesRegex(ValueError,'FIX'):
            admit.advance_history(self.calls,{},at=START,due=['A','FIX'],
                 admitted=['A'],deferred=['FIX'])

    def test_replay_rejects_deferring_previously_scheduled_call(self):
        recorded=admit.advance_history(self.calls,{},at=START,due=['A','B'],
                                        admitted=['A'],deferred=['B'])
        with self.assertRaisesRegex(ValueError,'previously scheduled'):
            admit.verify_history(self.calls,{},recorded,at=START,due=['A','B'],
                                 admitted=['A'],deferred=['B'],protected=(),previous_schedule=['B'])

    def test_unscheduled_auto_is_not_a_commitment_but_manual_lock_is(self):
        from src.daily_planning.integrated_v3.v5_locks import resolve_locks
        call=self.calls['A']
        automatic=resolve_locks([call],as_of_at=START,allow_unscheduled_auto=True)
        self.assertEqual(automatic['issues'],[])
        self.assertEqual(automatic['decisions']['A']['origin'],'UNSCHEDULED_NOT_PRIOR_COMMITMENT')
        manual=resolve_locks([call],as_of_at=START,lock_state={'A':{'mode':'MANUAL_LOCK'}},allow_unscheduled_auto=True)
        self.assertEqual(manual['issues'][0]['code'],'LOCK_REQUIRES_EXISTING_SCHEDULE')

if __name__=='__main__':unittest.main()
