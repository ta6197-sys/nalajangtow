import copy
import unittest
from src.daily_planning.integrated_v3.settlement import settle_v3_call

class V3ContractAdapterTest(unittest.TestCase):
    def setUp(self):
        self.call={'call_id':'C','contract_id':'C','vessel_id':'V','cargo_quantity_mt':'100','allowed_hours':'1','workbook_allowed_hours_exact':'1','nor_tender_at':'2026-11-03T00:00:00+09:00','nor_valid':'Y','notice_hours':'0','day_mode':'SHINC','weather_policy':'EXCLUDE','post_demurrage_exceptions':'NONE','early_start_if_work':'N','daily_demurrage_usd':'1440','contract_holidays':[]}
        self.source={'case_id':'C','vessel_id':'V','quantity_mt':'100','laytime_basis':'FIXED_HOURS','allowance_value':'1','nor_tender_local':self.call['nor_tender_at'],'nor_valid':'Y','notice_hours':'0','day_mode':'SHINC','weather_policy':'EXCLUDE','post_demurrage_exceptions':'NONE','early_start_if_work':'N','demurrage_usd_day':'1440'}
        self.snapshot={'calls':[self.call],'workbook_tables':{'contracts':{'rows':[self.source]}},'source_input':{'data_origin':'SYNTHETIC_FIXTURE','operational_approval':False,'planning_policy':{'laycan_exception':'SCENARIOS_ONLY_NOT_ACTIVE_IN_SETTLEMENT'}}}
        self.kw={'work_start_at':'2026-11-03T00:00:00+09:00','finish_at':'2026-11-03T02:00:00+09:00','event_intervals':[('2026-11-03T00:00:00+09:00','2026-11-03T02:00:00+09:00','WAIT')],'despatch_policy':'NONE','despatch_policy_reference':'synthetic unit fixture'}
    def evaluate(self):return settle_v3_call(self.snapshot,'C',**self.kw)
    def test_authoritative_evaluator_and_no_mutation(self):
        old=copy.deepcopy((self.snapshot,self.kw));r=self.evaluate();self.assertEqual(r['status'],'SETTLED_MINUTE_POLICY',r);self.assertEqual(r['settlement']['demurrage_minutes'],60);self.assertEqual(r['settlement']['demurrage_cents'],6000);self.assertFalse(r['operational_approval']);self.assertEqual((self.snapshot,self.kw),old)
    def test_second_precision_rejected(self):
        self.kw['finish_at']='2026-11-03T02:00:01+09:00';self.assertEqual(self.evaluate()['issues'][0]['code'],'UNSUPPORTED_SUBMINUTE_SETTLEMENT')
    def test_fractional_export_unresolved(self):
        self.call['allowed_hours']='1.000001';self.assertEqual(self.evaluate()['issues'][0]['code'],'UNRESOLVED_SETTLEMENT_PRECISION')
    def test_missing_policy_not_invented(self):
        self.kw['despatch_policy']=None;self.assertEqual(self.evaluate()['issues'][0]['code'],'MISSING_SETTLEMENT_POLICY')
    def test_uncovered_events_fail(self):
        self.kw['event_intervals']=[];self.assertEqual(self.evaluate()['status'],'UNRESOLVED')
    def test_contract_mismatch_rejected(self):
        self.source['allowance_value']='2';self.assertEqual(self.evaluate()['issues'][0]['code'],'CONTRACT_TERMS_MISMATCH')
    def test_laycan_policy_cannot_silently_change(self):
        self.snapshot['source_input']['planning_policy']['laycan_exception']='ACTIVE';self.assertEqual(self.evaluate()['issues'][0]['code'],'UNSUPPORTED_SETTLEMENT_POLICY')
    def test_subminute_laytime_rejected(self):
        self.call.update(allowed_hours='1/7',workbook_allowed_hours_exact='1/7');self.source['allowance_value']='1/7';self.assertEqual(self.evaluate()['issues'][0]['code'],'UNSUPPORTED_SUBMINUTE_LAYTIME')
    def test_weather_exclusion_is_evaluated_by_existing_rules(self):
        self.kw['event_intervals']=[('2026-11-03T00:00:00+09:00','2026-11-03T00:30:00+09:00','WEATHER'),('2026-11-03T00:30:00+09:00','2026-11-03T02:00:00+09:00','WAIT')]
        r=self.evaluate();self.assertEqual(r['status'],'SETTLED_MINUTE_POLICY',r);self.assertEqual(r['settlement']['pre_excluded_minutes'],30);self.assertEqual(r['settlement']['demurrage_cents'],3000)
    def test_second_policy_preserves_minute_results(self):
        legacy=self.evaluate();self.kw['settlement_policy']='SECOND_PRECISION_SIMULATION_V1';second=self.evaluate()
        self.assertEqual(second['status'],'SETTLED_SECOND_SIMULATION',second);self.assertEqual(second['settlement']['demurrage_cents'],legacy['settlement']['demurrage_cents']);self.assertEqual(second['settlement']['settlement_policy_version'],'SECOND_PRECISION_SIMULATION_V1')
    def test_second_policy_handles_seconds_without_rounding(self):
        self.kw['finish_at']='2026-11-03T02:00:01+09:00';self.kw['event_intervals'][0]=('2026-11-03T00:00:00+09:00','2026-11-03T02:00:01+09:00','WAIT');self.kw['settlement_policy']='SECOND_PRECISION_SIMULATION_V1'
        r=self.evaluate();self.assertEqual(r['status'],'SETTLED_SECOND_SIMULATION',r);self.assertEqual(r['settlement']['demurrage_seconds'],'3601');self.assertEqual(r['settlement']['demurrage_cents'],6002)
    def test_verified_rounded_json_uses_source_exact(self):
        self.source.update(laytime_basis='MT_PER_DAY',quantity_mt='100',allowance_value='100/ (unused)')
        self.source['allowance_value']='1800/19';self.call['allowed_hours']='25.333333';self.call['workbook_allowed_hours_exact']='76/3';self.kw['settlement_policy']='SECOND_PRECISION_SIMULATION_V1'
        r=self.evaluate();self.assertEqual(r['status'],'SETTLED_SECOND_SIMULATION',r);self.assertEqual(r['settlement']['allowed_minutes'],1520);self.assertTrue(r['verified_json_rounding_difference'])
    def test_second_laytime_exact_integer_seconds(self):
        self.source['allowance_value']='0.01';self.call.update(allowed_hours='0.01',workbook_allowed_hours_exact='1/100');self.kw['settlement_policy']='SECOND_PRECISION_SIMULATION_V1';self.kw['finish_at']='2026-11-03T00:02:00+09:00';self.kw['event_intervals']=[('2026-11-03T00:00:00+09:00','2026-11-03T00:02:00+09:00','WAIT')]
        r=self.evaluate();self.assertEqual(r['status'],'SETTLED_SECOND_SIMULATION',r);self.assertEqual(r['settlement']['laytime_used_seconds'],'36');self.assertEqual(r['settlement']['demurrage_seconds'],'60');self.assertEqual(r['settlement']['demurrage_cents'],100)
    def test_second_laytime_fractional_second_remains_exact(self):
        self.call.update(allowed_hours='1/7',workbook_allowed_hours_exact='1/7');self.source['allowance_value']='1/7';self.kw['settlement_policy']='SECOND_PRECISION_SIMULATION_V1'
        r=self.evaluate();self.assertEqual(r['status'],'SETTLED_SECOND_SIMULATION',r);self.assertEqual(r['settlement']['allowed_seconds_exact'],'3600/7');self.assertEqual(r['settlement']['laytime_precision'],'EXACT_RATIONAL_SOURCE_V1')
    def test_second_policy_rejects_non_synthetic_data(self):
        self.snapshot['source_input']['data_origin']='OPERATIONAL';self.kw['settlement_policy']='SECOND_PRECISION_SIMULATION_V1'
        self.assertEqual(self.evaluate()['issues'][0]['code'],'UNSUPPORTED_SETTLEMENT_POLICY')
