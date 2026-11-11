import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import timedelta
from tests.test_v5_cost_search import fixture,START
from src.daily_planning.integrated_input.validator import timestamp
from src.daily_planning.integrated_v3.v5_rolling import run_v5_rolling


def rolling_fixture():
    s,p=fixture();base=s['calls'][0];source=s['workbook_tables']['contracts']['rows'][0];bid=s['berths'][0]['berth_id']
    p['berths'][bid].update(berthing_seconds=3600,hose_connection_seconds=7200,unberthing_seconds=3600,tug_units=1);p['tug_capacity']=90
    rows=[];contracts=[]
    for i in range(3):
        c=copy.deepcopy(base);cid='SYN-'+str(i+1);eta=(timestamp(START)+timedelta(hours=9+24*i)).isoformat();c.update(call_id=cid,vessel_id='V'+str(i+1),eta_at=eta,nor_tender_at=eta)
        t=copy.deepcopy(source);t.update(case_id=cid,vessel_id=c['vessel_id'],nor_tender_local=eta);rows.append(c);contracts.append(t)
    s['calls']=rows;s['workbook_tables']['contracts']['rows']=contracts
    for tid,group in [('U','Y'),('V','Z')]:
        tank=copy.deepcopy(s['tanks'][0]);tank.update(tank_id=tid,cargo_group=group,connected_berth_ids=[]);s['tanks'].append(tank)
    s['fixed_process_flows']={'inventory_as_of_at':START,'segments':[{'tank_id':t['tank_id'],'start_at':START,'end_at':'2026-11-10T00:00:00+09:00','fixed_net_flow_bbl_per_hour':'0'} for t in s['tanks']]}
    window={'start_at':START,'end_at':'2026-11-10T00:00:00+09:00'}
    p['berths'][bid]['work_windows']=[window];p['berths'][bid]['pilotage_windows']=[window]
    ctx={'daylight_path':'src/daily_planning/daylight_windows.csv','daylight_source':'Repository Ulsan daily daylight','night_berthing_permissions':{bid:True},'departure_windows_by_berth_call':{bid:{c['call_id']:[window] for c in rows}}}
    return s,p,ctx

class V5RollingTests(unittest.TestCase):
    def setUp(self):
        self.s,self.p,self.ctx=rolling_fixture();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.folder=Path(self.tmp.name)/'run'
    def run_roll(self,**kw):
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *a:copy.deepcopy(self.s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}):
            return run_v5_rolling(self.s,data_dir='ISOLATED_SYNTHETIC_TEST',policy=self.p,pilotage_context=self.ctx,as_of_at=START,output_dir=self.folder,pricing_policy='CLEAR_WEATHER_BASELINE_V1',windows=3,max_cost_candidates=1,time_limit_seconds=30,**kw)
    def test_three_windows_and_no_double_count(self):
        r=self.run_roll();self.assertEqual(r['status'],'REQUESTED_WINDOWS_VALIDATED',r);self.assertEqual(r['validated_window_count'],3);self.assertEqual(r['completed_call_ids'],['SYN-1','SYN-2','SYN-3']);self.assertEqual(r['carried_inventory'],{'T':'800','U':'500','V':'500'});self.assertEqual(len(list(self.folder.glob('window_*.json'))),3)
        middle=json.loads((self.folder/'window_002.json').read_text());last=json.loads((self.folder/'window_003.json').read_text());self.assertNotIn('SYN-1',[x['call_id'] for x in middle['selected_schedule']]);self.assertEqual(len(last['contract_accounting_state']),3);self.assertEqual(r['cumulative_demurrage_cents'],sum(x['demurrage_cents'] for x in last['contract_accounting_state'].values()))
    def test_pause_resume_and_tamper(self):
        first=self.run_roll(max_new_windows=1);self.assertEqual(first['status'],'PAUSED',first);second=self.run_roll(resume=True);self.assertEqual(second['validated_window_count'],3,second)
        path=self.folder/'window_001.json';data=json.loads(path.read_text());data['next_state']['inventory']['T']='0';path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'fingerprint|state mismatch'):self.run_roll(resume=True)
    def test_auto_lock_without_schedule_stops(self):
        self.s['calls'][0]['eta_at']='2026-11-03T05:00:00+09:00';r=self.run_roll();self.assertEqual(r['validated_window_count'],0);self.assertEqual(r['stopping_reason']['code'],'DYNAMIC_LOCK_PENDING')
    def test_unresolved_settlement_stops_before_checkpoint(self):
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *a:copy.deepcopy(self.s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}):
            r=run_v5_rolling(self.s,data_dir='SYNTHETIC',policy=self.p,pilotage_context=self.ctx,as_of_at=START,output_dir=self.folder,windows=1,max_cost_candidates=1,time_limit_seconds=30,pricing_policy='EXPLICIT_EVENTS_V1')
        self.assertEqual(r['validated_window_count'],0);self.assertFalse((self.folder/'window_001.json').exists())
    def test_historical_clock_difference_is_unresolved_not_invalid_contract(self):
        self.s['calls'][0]['call_id']='DEM-0011';self.s['workbook_tables']['contracts']['rows'][0]['case_id']='DEM-0011';bid=self.s['berths'][0]['berth_id'];self.ctx['departure_windows_by_berth_call'][bid]['DEM-0011']=self.ctx['departure_windows_by_berth_call'][bid].pop('SYN-1')
        r=self.run_roll();self.assertEqual(r['validated_window_count'],0);self.assertEqual(r['stopping_reason']['optimization_status'],'SETTLEMENT_UNRESOLVED');self.assertFalse((self.folder/'window_001.json').exists())
    def test_inadequate_budget_stops(self):
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *a:copy.deepcopy(self.s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}):
            r=run_v5_rolling(self.s,data_dir='SYNTHETIC',policy=self.p,pilotage_context=self.ctx,as_of_at=START,output_dir=self.folder,windows=1,max_cost_candidates=1,time_limit_seconds=1e-12,pricing_policy='CLEAR_WEATHER_BASELINE_V1')
        self.assertEqual(r['validated_window_count'],0);self.assertFalse((self.folder/'window_001.json').exists())
    def test_empty_window_replays_signed_external_flow(self):
        for c,contract in zip(self.s['calls'],self.s['workbook_tables']['contracts']['rows']):
            c['eta_at']='2026-11-07T09:00:00+09:00';c['nor_tender_at']=c['eta_at'];contract['nor_tender_local']=c['eta_at']
        self.s['fixed_process_flows']['segments'][0]['fixed_net_flow_bbl_per_hour']='-10'
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *a:copy.deepcopy(self.s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}):
            r=run_v5_rolling(self.s,data_dir='SYNTHETIC',policy=self.p,pilotage_context=self.ctx,as_of_at=START,output_dir=self.folder,windows=1,max_cost_candidates=1,time_limit_seconds=30,pricing_policy='CLEAR_WEATHER_BASELINE_V1')
        self.assertEqual(r['status'],'REQUESTED_WINDOWS_VALIDATED',r);self.assertEqual(r['carried_inventory']['T'],'260');self.assertEqual(r['window_results'][0]['selected_call_ids'],[]);self.assertEqual(r['completed_call_ids'],[])
    def test_invalid_inventory_never_advances(self):
        self.s['tanks'][0]['initial_inventory_bbl']='1001';r=self.run_roll();self.assertEqual(r['validated_window_count'],0);self.assertFalse((self.folder/'window_001.json').exists())
    def test_changed_policy_refused_on_resume(self):
        self.assertEqual(self.run_roll(max_new_windows=1)['status'],'PAUSED');self.p['tug_capacity']=89
        with self.assertRaisesRegex(ValueError,'fingerprint'):self.run_roll(resume=True)
    def test_manual_lock_is_hard_in_next_window(self):
        self.s['calls']=self.s['calls'][:2];self.s['workbook_tables']['contracts']['rows']=self.s['workbook_tables']['contracts']['rows'][:2]
        first=self.run_roll(max_new_windows=1);self.assertEqual(first['status'],'PAUSED',first);before=json.loads((self.folder/'window_001.json').read_text());row=next(x for x in before['selected_schedule'] if x['call_id']=='SYN-2')
        r=self.run_roll(resume=True,max_new_windows=1,manual_overrides={'SYN-2':'MANUAL_LOCK'});self.assertEqual(r['status'],'PAUSED',r);second=json.loads((self.folder/'window_002.json').read_text());self.assertIn(row,second['protected_commitments_in']);self.assertIn(row,second['selected_schedule']);self.assertEqual(second['summary']['lock_decisions']['SYN-2']['reason'],'MANUAL_LOCK')
    def test_waiting_call_is_retained_and_locked(self):
        self.s['calls']=self.s['calls'][:2];self.s['workbook_tables']['contracts']['rows']=self.s['workbook_tables']['contracts']['rows'][:2]
        self.s['calls'][1].update(eta_at='2026-11-03T23:00:00+09:00',nor_tender_at='2026-11-03T23:00:00+09:00');self.s['workbook_tables']['contracts']['rows'][1]['nor_tender_local']=self.s['calls'][1]['nor_tender_at']
        self.s['workbook_tables']['restrictions']['rows']=[{'berth_id':self.s['berths'][0]['berth_id'],'start_local':'2026-11-03T22:00:00+09:00','end_local':'2026-11-04T08:00:00+09:00','restriction_scope':'BOTH','cargo_group':None}]
        first=self.run_roll(max_new_windows=1);self.assertEqual(first['status'],'PAUSED',first);self.assertIn('SYN-2',first['waiting_call_ids']);self.assertIn('SYN-2',first['protected_call_ids']);self.assertIsNone(first['cumulative_demurrage_cents'])
        original=next(x for x in json.loads((self.folder/'window_001.json').read_text())['selected_schedule'] if x['call_id']=='SYN-2')
        second=self.run_roll(resume=True,max_new_windows=1);self.assertEqual(second['status'],'PAUSED',second);cp=json.loads((self.folder/'window_002.json').read_text());self.assertIn(original,cp['protected_commitments_in']);self.assertIn(original,cp['selected_schedule'])
    def test_ongoing_transfer_across_boundary_and_exact_accounting(self):
        self.s['calls']=self.s['calls'][:1];self.s['workbook_tables']['contracts']['rows']=self.s['workbook_tables']['contracts']['rows'][:1]
        self.s['calls'][0].update(cargo_quantity_mt='30',cargo_volume_bbl='300');self.s['workbook_tables']['contracts']['rows'][0]['quantity_mt']='30';self.s['berths'][0]['max_transfer_rate_mt_h']='1'
        bid=self.s['berths'][0]['berth_id'];self.p['berths'][bid]['work_windows']=[{'start_at':START,'end_at':'2026-11-04T20:00:00+09:00'}]
        first=self.run_roll(max_new_windows=1);self.assertEqual(first['status'],'PAUSED',first);self.assertEqual(first['ongoing_call_ids'],['SYN-1']);self.assertEqual(first['protected_call_ids'],['SYN-1']);self.assertEqual(len(first['carried_transfers']),1)
        from fractions import Fraction
        progress=first['cargo_progress']['SYN-1'];self.assertGreater(Fraction(progress['completed_bbl']),0);self.assertEqual(Fraction(progress['completed_bbl'])+Fraction(progress['remaining_bbl']),300);self.assertEqual(Fraction(progress['remaining_bbl']),Fraction(first['carried_transfers'][0]['remaining_bbl']));self.assertEqual(first['in_progress_contract_accounting']['SYN-1']['status'],'IN_PROGRESS');self.assertEqual(first['cumulative_demurrage_cents'],first['in_progress_contract_accounting']['SYN-1']['accrued_demurrage_cents'])
        second=self.run_roll(resume=True);self.assertEqual(second['status'],'REQUESTED_WINDOWS_VALIDATED',second);self.assertEqual(second['completed_call_ids'],['SYN-1']);self.assertEqual(second['carried_inventory']['T'],'800');cp=json.loads((self.folder/'window_002.json').read_text());self.assertEqual(cp['next_state']['cargo_progress']['SYN-1'],{'completed_bbl':'300','remaining_bbl':'0'});self.assertEqual(len(cp['contract_accounting_state']),1);self.assertEqual(second['cumulative_demurrage_cents'],cp['contract_accounting_state']['SYN-1']['demurrage_cents'])
    def test_dynamic_lock_keeps_previously_planned_vessel(self):
        self.s['calls']=self.s['calls'][:2];self.s['workbook_tables']['contracts']['rows']=self.s['workbook_tables']['contracts']['rows'][:2]
        self.s['calls'][1]['eta_at']='2026-11-04T04:00:00+09:00';self.s['calls'][1]['nor_tender_at']=self.s['calls'][1]['eta_at'];self.s['workbook_tables']['contracts']['rows'][1]['nor_tender_local']=self.s['calls'][1]['eta_at']
        self.s['workbook_tables']['restrictions']['rows']=[{'berth_id':self.s['berths'][0]['berth_id'],'start_local':'2026-11-04T02:00:00+09:00','end_local':'2026-11-04T08:00:00+09:00','restriction_scope':'BOTH','cargo_group':None}]
        first=self.run_roll(max_new_windows=1);self.assertEqual(first['status'],'PAUSED',first);self.assertIn('SYN-2',first['protected_call_ids']);before=json.loads((self.folder/'window_001.json').read_text());original=next(r for r in before['selected_schedule'] if r['call_id']=='SYN-2')
        second=self.run_roll(resume=True,max_new_windows=1);self.assertEqual(second['status'],'PAUSED',second);after=json.loads((self.folder/'window_002.json').read_text());self.assertIn(original,after['protected_commitments_in']);self.assertIn(original,after['selected_schedule'])
    def test_manual_unlock_persists_on_resume(self):
        self.s['calls']=self.s['calls'][:2];self.s['workbook_tables']['contracts']['rows']=self.s['workbook_tables']['contracts']['rows'][:2]
        self.s['calls'][1]['eta_at']='2026-11-04T04:00:00+09:00';self.s['calls'][1]['nor_tender_at']=self.s['calls'][1]['eta_at'];self.s['workbook_tables']['contracts']['rows'][1]['nor_tender_local']=self.s['calls'][1]['eta_at']
        self.s['workbook_tables']['restrictions']['rows']=[{'berth_id':self.s['berths'][0]['berth_id'],'start_local':'2026-11-04T02:00:00+09:00','end_local':'2026-11-04T08:00:00+09:00','restriction_scope':'BOTH','cargo_group':None}]
        self.assertEqual(self.run_roll(max_new_windows=1)['status'],'PAUSED');r=self.run_roll(resume=True,max_new_windows=1,manual_overrides={'SYN-2':'MANUAL_UNLOCK'});self.assertEqual(r['status'],'PAUSED',r);cp=json.loads((self.folder/'window_002.json').read_text());self.assertEqual(cp['summary']['lock_decisions']['SYN-2']['status'],'CHANGEABLE');self.assertEqual(cp['next_state']['lock_state']['SYN-2']['mode'],'MANUAL_UNLOCK');self.assertEqual(self.run_roll(resume=True)['status'],'REQUESTED_WINDOWS_VALIDATED')
    def test_fix_etb_preserved_during_manual_unlock(self):
        self.s['calls']=self.s['calls'][:2];self.s['workbook_tables']['contracts']['rows']=self.s['workbook_tables']['contracts']['rows'][:2]
        c=self.s['calls'][1];c.update(eta_at='2026-11-04T04:00:00+09:00',nor_tender_at='2026-11-04T04:00:00+09:00',fixed_etb_is_hard=True,fix_etb_at='2026-11-04T10:00:00+09:00');self.s['workbook_tables']['contracts']['rows'][1]['nor_tender_local']=c['nor_tender_at']
        self.assertEqual(self.run_roll(max_new_windows=1)['status'],'PAUSED');r=self.run_roll(resume=True,max_new_windows=1,manual_overrides={'SYN-2':'MANUAL_UNLOCK'});self.assertEqual(r['status'],'PAUSED',r);cp=json.loads((self.folder/'window_002.json').read_text());row=next(x for x in cp['selected_schedule'] if x['call_id']=='SYN-2');self.assertEqual(timestamp(row['etb_at']),timestamp(c['fix_etb_at']))
    def test_operator_approved_future_commitment_survives_manual_unlock(self):
        from src.daily_planning.physical_scheduling.phases import replay
        row=replay(self.s['calls'][0],self.s['berths'][0],'2026-11-03T10:00:00+09:00',self.p);r=self.run_roll(max_new_windows=1,approved_commitments=[row],manual_overrides={'SYN-1':'MANUAL_UNLOCK'});self.assertEqual(r['status'],'PAUSED',r)
        cp=json.loads((self.folder/'window_001.json').read_text());self.assertIn(row,cp['protected_commitments_in']);self.assertIn(row,cp['selected_schedule']);self.assertEqual(cp['summary']['lock_decisions']['SYN-1']['origin'],'OPERATOR_APPROVED_COMMITMENT')
    def test_completed_transfer_while_physical_phase_continues_not_replayed(self):
        from src.daily_planning.physical_scheduling.phases import replay
        from src.daily_planning.integrated_v3.coordinator import validate_v5_candidate
        row=replay(self.s['calls'][0],self.s['berths'][0],'2026-11-03T20:59:00+09:00',self.p);boundary='2026-11-04T00:00:00+09:00';self.assertLess(timestamp(row['work_end_at']),timestamp(boundary));self.assertGreater(timestamp(row['physical_clearance_at']),timestamp(boundary))
        first=validate_v5_candidate(self.s,[row],as_of_at=START,projection_end_at=boundary);self.assertEqual(first['status'],'INVENTORY_VALID',first)
        state=first['tank_result']['end_state'];second=validate_v5_candidate(self.s,[row],as_of_at=boundary,initial_inventory=state['inventory_bbl'],physical_commitments=[row]);self.assertEqual(second['status'],'INVENTORY_VALID',second);self.assertEqual(second['tank_result']['assignments'],[]);self.assertEqual(second['tank_result']['end_state']['inventory_bbl']['T'],'600')

class V5RealRollingTest(unittest.TestCase):
    def test_real_v5_small_subset_three_windows(self):
        import hashlib
        from tests import test_v5_adaptive_candidates as adaptive
        from src.daily_planning.integrated_v3.v5_physical import schedule_v5
        captured=[]
        def capture(*args,**kwargs):
            result=schedule_v5(*args,**kwargs);captured.append((args,kwargs));return result
        with patch.object(adaptive,'schedule_v5',side_effect=capture):adaptive.RealAdaptiveTest('test_actual_subset_automatic').test_actual_subset_automatic()
        if not captured:self.skipTest('Actual V5 inputs unavailable')
        args,kw=captured[0];root=Path(kw['data_dir']);before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
        with tempfile.TemporaryDirectory() as folder:
            options={'data_dir':root,'policy':kw['policy'],'pilotage_context':kw['pilotage_context'],'as_of_at':kw['as_of_at'],'output_dir':Path(folder)/'run','call_ids':kw['call_ids'],'windows':3,'time_limit_seconds':30,'max_cost_candidates':1,'pricing_policy':'CLEAR_WEATHER_BASELINE_V1'}
            first=run_v5_rolling(*args,**options,max_new_windows=1);self.assertEqual(first['status'],'PAUSED',first)
            r=run_v5_rolling(*args,**options,resume=True)
            self.assertEqual(r['status'],'REQUESTED_WINDOWS_VALIDATED',r);self.assertEqual(r['validated_window_count'],3);self.assertEqual(r['completed_call_ids'],['DEM-0103','DEM-0112']);self.assertEqual(r['cumulative_demurrage_cents'],0);self.assertEqual(r['state_origin'],'SIMULATED_PROJECTION_NOT_OBSERVATION')
            cp=json.loads((Path(folder)/'run/window_001.json').read_text());fixed=next(x for x in cp['selected_schedule'] if x['call_id']=='DEM-0103');self.assertEqual(timestamp(fixed['etb_at']),timestamp('2026-11-03T20:30:00+09:00'))
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()})
