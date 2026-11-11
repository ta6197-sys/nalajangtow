import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.test_v5_rolling import rolling_fixture,START
from src.daily_planning.integrated_v3.v5_rolling import run_v5_rolling


class IntegratedRollingTest(unittest.TestCase):
    def setUp(self):
        self.s,self.p,self.ctx=rolling_fixture();b=copy.deepcopy(self.s['berths'][0]);b['berth_id']='B2';self.s['berths'].append(b)
        self.s['tanks'][0]['connected_berth_ids'].append('B2');self.p['berths']['B2']=copy.deepcopy(self.p['berths'][self.s['berths'][0]['berth_id']])
        self.ctx['night_berthing_permissions']['B2']=True;self.ctx['departure_windows_by_berth_call']['B2']=copy.deepcopy(self.ctx['departure_windows_by_berth_call'][self.s['berths'][0]['berth_id']])
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'run'
    def run_roll(self,**kwargs):
        options=dict(data_dir='SYNTHETIC',policy=self.p,pilotage_context=self.ctx,as_of_at=START,output_dir=self.path,windows=3,time_limit_seconds=30,pricing_policy='CLEAR_WEATHER_BASELINE_V1',rolling_optimizer_mode='INTEGRATED_COST_AWARE_V1')
        options.update(kwargs)
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *a:copy.deepcopy(self.s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}):
            return run_v5_rolling(self.s,**options)
    def test_two_windows_then_resume_third(self):
        first=self.run_roll(max_new_windows=2);self.assertEqual(first['status'],'PAUSED',first);self.assertEqual(first['validated_window_count'],2)
        second=self.run_roll(resume=True);self.assertEqual(second['status'],'REQUESTED_WINDOWS_VALIDATED',second);self.assertEqual(second['validated_window_count'],3)
        self.assertEqual(second['completed_call_ids'],['SYN-1','SYN-2','SYN-3']);self.assertEqual(second['carried_inventory'],{'T':'800','U':'500','V':'500'})
        self.assertTrue(all(x['rolling_optimizer_mode']=='INTEGRATED_COST_AWARE_V1' for x in second['window_results']))
        last=json.loads((self.path/'window_003.json').read_text());self.assertEqual(second['cumulative_demurrage_cents'],sum(v['demurrage_cents'] for v in last['contract_accounting_state'].values()))
    def test_resume_changed_mode_or_limits_rejected(self):
        self.assertEqual(self.run_roll(max_new_windows=1)['status'],'PAUSED')
        with self.assertRaisesRegex(ValueError,'fingerprint'):self.run_roll(resume=True,rolling_optimizer_mode='LEGACY_ENUMERATION')
        with self.assertRaisesRegex(ValueError,'fingerprint'):self.run_roll(resume=True,max_improvement_rounds=1)
    def test_resume_matches_uninterrupted_final_state(self):
        self.assertEqual(self.run_roll(max_new_windows=1)['status'],'PAUSED');resumed=self.run_roll(resume=True)
        saved=json.loads((self.path/'window_003.json').read_text())['next_state']
        with tempfile.TemporaryDirectory() as folder:
            self.path=Path(folder)/'run';fresh=self.run_roll();other=json.loads((self.path/'window_003.json').read_text())['next_state']
        self.assertEqual(resumed['status'],'REQUESTED_WINDOWS_VALIDATED');self.assertEqual(fresh['status'],'REQUESTED_WINDOWS_VALIDATED');self.assertEqual(saved,other)
    def test_invalid_result_does_not_advance(self):
        with patch('src.daily_planning.integrated_v3.v5_rolling_optimizer.v5_physical.schedule_v5',return_value={'status':'TIME_LIMIT_NO_INCUMBENT','issues':[]}):r=self.run_roll()
        self.assertEqual(r['validated_window_count'],0);self.assertFalse((self.path/'window_001.json').exists())
    def test_failed_second_window_preserves_checkpoint(self):
        self.assertEqual(self.run_roll(max_new_windows=1)['status'],'PAUSED');before=(self.path/'window_001.json').read_bytes()
        with patch('src.daily_planning.integrated_v3.v5_rolling_optimizer.v5_physical.schedule_v5',return_value={'status':'INFEASIBLE','issues':[]}):r=self.run_roll(resume=True)
        self.assertEqual(r['validated_window_count'],1);self.assertEqual((self.path/'window_001.json').read_bytes(),before);self.assertFalse((self.path/'window_002.json').exists())
    def test_corrupted_checkpoint_rejected(self):
        self.assertEqual(self.run_roll(max_new_windows=1)['status'],'PAUSED');path=self.path/'window_001.json';cp=json.loads(path.read_text());cp['carried_inventory']['T']='0';path.write_text(json.dumps(cp))
        with self.assertRaisesRegex(ValueError,'fingerprint'):self.run_roll(resume=True)
    def test_unresolved_settlement_does_not_advance(self):
        r=self.run_roll(pricing_policy='EXPLICIT_EVENTS_V1');self.assertEqual(r['validated_window_count'],0);self.assertFalse((self.path/'window_001.json').exists())
    def test_integrated_failure_never_falls_back(self):
        for status in ('INFEASIBLE','UNSUPPORTED_OPERATIONAL_POLICY','INVENTORY_MODEL_VALIDATION_MISMATCH','INVALID_INPUT'):
            with self.subTest(status=status),tempfile.TemporaryDirectory() as folder:
                self.path=Path(folder)/'run'
                with patch('src.daily_planning.integrated_v3.v5_rolling_optimizer.v5_physical.schedule_v5',return_value={'status':status,'issues':[]}),patch('src.daily_planning.integrated_v3.v5_rolling.optimize_v5',side_effect=AssertionError('legacy fallback')):
                    result=self.run_roll()
                self.assertEqual(result['validated_window_count'],0);self.assertEqual(result['stopping_reason']['reason'],status);self.assertFalse((self.path/'window_001.json').exists())
    def test_validated_incumbent_survives_expansion_timeout(self):
        from src.daily_planning.integrated_v3.v5_physical import schedule_v5
        def timeout_after_valid(*args,**kwargs):
            result=schedule_v5(*args,**kwargs);result['stopping_reason']='TIME_BUDGET_EXHAUSTED';result['final_domain_optimality_proven']=False;result['optimality_scope']='BEST_VALIDATED_INCUMBENT_EXPANDED_DOMAIN_UNPROVEN';return result
        with patch('src.daily_planning.integrated_v3.v5_rolling_optimizer.v5_physical.schedule_v5',side_effect=timeout_after_valid):r=self.run_roll(max_new_windows=1)
        self.assertEqual(r['status'],'PAUSED',r);self.assertEqual(r['validated_window_count'],1);self.assertFalse(r['window_results'][0]['final_domain_optimality_proven'])
    def test_invalid_carried_fingerprint_blocks_checkpoint(self):
        from src.daily_planning.integrated_v3.v5_rolling import _transition
        c=self.s['calls'][0];c.update(cargo_quantity_mt='30',cargo_volume_bbl='300',transfer_rate_mt_h='1');self.s['workbook_tables']['contracts']['rows'][0]['quantity_mt']='30'
        count=[]
        def tamper(*args,**kwargs):
            result=_transition(*args,**kwargs);count.append(1)
            if len(count)==1:result['carried_transfers'][0]['transfer_fingerprint']='corrupted'
            return result
        with patch('src.daily_planning.integrated_v3.v5_rolling._transition',side_effect=tamper):r=self.run_roll()
        self.assertEqual(r['validated_window_count'],0);self.assertFalse((self.path/'window_001.json').exists());self.assertEqual(r['stopping_reason']['code'],'CARRYOVER_MISMATCH')
    def test_independent_inventory_disagreement_blocks_checkpoint(self):
        failure={'status':'TANK_SUBPROBLEM_NOT_VALIDATED','issues':[{'code':'INVENTORY_OUT_OF_BOUNDS'}]}
        with patch('src.daily_planning.integrated_v3.v5_rolling.validate_v5_candidate',return_value=failure):r=self.run_roll()
        self.assertEqual(r['validated_window_count'],0);self.assertFalse((self.path/'window_001.json').exists());self.assertEqual(r['stopping_reason']['code'],'INDEPENDENT_REPLAY_FAILED')
    def test_changed_physical_commitment_blocks_advancement(self):
        from src.daily_planning.integrated_v3.v5_physical import schedule_v5
        self.assertEqual(self.run_roll(max_new_windows=1)['status'],'PAUSED')
        def tamper(*args,**kwargs):
            result=schedule_v5(*args,**kwargs)
            if result['status']=='PHYSICAL_AND_INVENTORY_VALID':
                cid=kwargs['physical_commitments'][0]['call_id']
                row=next(x for x in result['selected_schedule'] if x['call_id']==cid);row['physical_clearance_at']='2026-11-09T00:00:00+09:00'
            return result
        with patch('src.daily_planning.integrated_v3.v5_rolling_optimizer.v5_physical.schedule_v5',side_effect=tamper):r=self.run_roll(resume=True,manual_overrides={'SYN-2':'MANUAL_LOCK'})
        self.assertEqual(r['validated_window_count'],1);self.assertFalse((self.path/'window_002.json').exists())
    def test_ongoing_cargo_dynamic_lock_and_fix(self):
        self.s['tanks'][0]['capacity_bbl']='1200'
        extra=copy.deepcopy(self.s['calls'][0]);extra.update(call_id='SYN-4',vessel_id='V4');self.s['calls'].append(extra)
        contract=copy.deepcopy(self.s['workbook_tables']['contracts']['rows'][0]);contract.update(case_id='SYN-4',vessel_id='V4');self.s['workbook_tables']['contracts']['rows'].append(contract)
        for berth in self.ctx['departure_windows_by_berth_call'].values():berth['SYN-4']=copy.deepcopy(berth['SYN-1'])
        c=self.s['calls'][0];c.update(cargo_quantity_mt='30',cargo_volume_bbl='300',transfer_rate_mt_h='1');self.s['workbook_tables']['contracts']['rows'][0]['quantity_mt']='30'
        fixed=self.s['calls'][1];fixed.update(eta_at='2026-11-04T04:00:00+09:00',nor_tender_at='2026-11-04T04:00:00+09:00',fixed_etb_is_hard=True,fix_etb_at='2026-11-04T10:00:00+09:00');self.s['workbook_tables']['contracts']['rows'][1]['nor_tender_local']=fixed['nor_tender_at']
        future=self.s['calls'][2];future.update(eta_at='2026-11-06T12:00:00+09:00',nor_tender_at='2026-11-06T12:00:00+09:00');self.s['workbook_tables']['contracts']['rows'][2]['nor_tender_local']=future['nor_tender_at']
        first=self.run_roll(max_new_windows=1);self.assertEqual(first['status'],'PAUSED',first);self.assertIn('SYN-4',first['completed_call_ids']);self.assertIn('SYN-1',first['ongoing_call_ids']);self.assertIn('SYN-1',first['protected_call_ids']);self.assertTrue(first['carried_transfers']);self.assertNotIn('SYN-3',first['window_results'][0]['selected_call_ids'])
        first_cp=json.loads((self.path/'window_001.json').read_text());before=next(r for r in first_cp['selected_schedule'] if r['call_id']=='SYN-1')
        result=self.run_roll(resume=True);self.assertEqual(result['status'],'REQUESTED_WINDOWS_VALIDATED',result)
        second=json.loads((self.path/'window_002.json').read_text());self.assertIn(before,second['protected_commitments_in']);self.assertIn(before,second['selected_schedule']);self.assertIn('SYN-3',second['summary']['selected_call_ids'])
        fixed_row=next(r for r in second['selected_schedule'] if r['call_id']=='SYN-2');self.assertEqual(fixed_row['etb_at'],fixed['fix_etb_at'])
        self.assertEqual(result['cargo_progress']['SYN-1'],{'completed_bbl':'300','remaining_bbl':'0'});self.assertEqual(result['carried_inventory']['T'],'1000');self.assertIn('SYN-3',result['remaining_call_ids'])
