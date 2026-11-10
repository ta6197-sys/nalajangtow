import copy
import unittest
from unittest.mock import patch
from src.daily_planning.integrated_v3.rolling_optimizer import run_costed_rolling

class CostedRollingTest(unittest.TestCase):
    def test_two_days_preserve_state_and_price_each_window(self):
        from tests.test_v3_cost_optimizer import CostOptimizerTest
        f=CostOptimizerTest();f.setUp();s=f.s;p=f.p
        c=copy.deepcopy(s['calls'][0]);c.update(call_id='SYN-B',contract_id='SYN-B',vessel_id='SYN-W',eta_at='2026-11-04T01:00:00+09:00',nor_tender_at='2026-11-04T00:00:00+09:00');s['calls'].append(c)
        contract=copy.deepcopy(s['workbook_tables']['contracts']['rows'][0]);contract.update(case_id='SYN-B',vessel_id='SYN-W',nor_tender_local=c['nor_tender_at']);s['workbook_tables']['contracts']['rows'].append(contract)
        before=copy.deepcopy((s,p))
        with patch('src.daily_planning.integrated_input.validator.validate',return_value={'data_preflight_pass':True}):
            r=run_costed_rolling(s,as_of_at='2026-11-03T00:00:00+09:00',policy=p,days=2,time_limit_per_day=10,candidate_policies=('V3_ETB_EVENTS_V1',))
        self.assertEqual(r['status'],'REQUESTED_WINDOWS_VALIDATED',r)
        self.assertEqual(r['steps'][1]['completed_call_ids'],['SYN-A']);self.assertEqual((s,p),before);self.assertFalse(r['operational_approval'])
