"""No solver needed. Run: python -m unittest tests.test_v5_simulation_bridge_v2_pure -v"""
from __future__ import annotations
import base64
import copy
from datetime import timedelta
from hashlib import sha256
import unittest
from src.daily_planning.integrated_v3.v5_simulation_bridge_v2 import (
    fingerprint, initial_rolling_state, validate_admission, vessel_statuses,
    _source_contracts, verify_bundle_attestation, BridgeError, BRIDGE_VERSION)

AT='2026-11-03T00:00:00+09:00'
AFTER='2026-11-04T00:00:00+09:00'


def call(cid, eta, fixed=False):
    return {'call_id':cid,'vessel_id':'V-'+cid,'eta_at':eta,
            'fixed_etb_is_hard':fixed,'cargo_volume_bbl':'100'}

class BridgePureTests(unittest.TestCase):
    def test_first_state_exact_opt_in(self):
        s=initial_rolling_state(AT,deferral_enabled=True)
        self.assertEqual(s['at'],AT)
        self.assertIsNone(s['inventory'])
        self.assertEqual(s['deferral_history'],{})
        self.assertFalse(s['operational_approval'])

    def test_first_state_legacy_has_no_history(self):
        self.assertNotIn('deferral_history', initial_rolling_state(AT,deferral_enabled=False))

    def test_initial_time_requires_offset(self):
        with self.assertRaises(BridgeError):
            initial_rolling_state('2026-11-03T00:00:00', deferral_enabled=True)

    def test_statuses_partition_at_projected_boundary(self):
        snap={'calls':[call('F','2026-11-07T00:00:00+09:00'),
                       call('W','2026-11-03T09:00:00+09:00'),
                       call('O','2026-11-03T08:00:00+09:00'),
                       call('D','2026-11-03T07:00:00+09:00')]}
        cp={'next_state':{'at':AFTER, 'completed_rows':[{'call_id':'D'}],
            'previous_schedule':[{'call_id':'O','berthing_start_at':'2026-11-03T19:00:00+09:00'}],
            'cargo_progress':{},'contract_accounting_state':{},
            'in_progress_contract_accounting':{},'deferral_history':{}}}
        statuses={x['call_id']:x for x in vessel_statuses(snap,cp,{'F','W','O','D'})}
        self.assertEqual([statuses[c]['operational_status'] for c in ('F','W','O','D')],
            ['FUTURE_ETA','WAITING_ETA_BASED_NOT_OBSERVED','IN_PROGRESS_SIMULATED','COMPLETED'])
        self.assertTrue(all(x['status_origin']=='SIMULATED_PROJECTION_NOT_OBSERVATION'
                            for x in statuses.values()))

    def test_legacy_admission_no_false_deferral(self):
        cp={'selected_schedule':[{'call_id':'A'}]}
        a=validate_admission(cp,{'A':call('A',AT)},
                             prior_state={},config={})
        self.assertEqual(a['admitted_call_ids'],['A'])
        self.assertEqual(a['deferred_call_ids'],[])

    def test_legacy_cannot_silently_have_deferred(self):
        with self.assertRaises(BridgeError):
            validate_admission({'selected_schedule':[],'deferred_call_ids':['A']},
                {'A':call('A',AT)},prior_state={},config={})

    def test_duplicate_schedule_rejected(self):
        with self.assertRaises(BridgeError):
            validate_admission({'selected_schedule':[{'call_id':'A'},{'call_id':'A'}]},
                {'A':call('A',AT)},prior_state={},config={})

    def test_duplicate_contract_rejected(self):
        s={'workbook_tables':{'contracts':{'rows':[{'case_id':'A'},{'case_id':'A'}]}}}
        with self.assertRaises(BridgeError):
            _source_contracts(s,{'A'})

    def test_missing_contract_is_explicit(self):
        s={'workbook_tables':{'contracts':{'rows':[{'case_id':'A'}]}}}
        c=_source_contracts(s,{'A','B'})
        self.assertEqual(c['missing_contract_call_ids'],['B'])

    def fixture_bundle(self):
        snapshot={'calls':[call('A','2026-11-07T00:00:00+09:00')]}
        context={'daylight_path':'/repo/src/daily_planning/daylight_windows.csv'}
        policy={'schema_version':'SIMULATED'}
        daylight=b'date_kst,sunrise_kst,sunset_kst\n2026-11-03,06:44,17:28\n'
        sha=sha256(daylight).hexdigest()
        p_hash=fingerprint({'policy':policy,'pilotage_context':context,
           'daylight_sha256':sha,'pricing_policy':'CLEAR_WEATHER_BASELINE_V1',
           'clock_policy':'BLOCK_UNVERIFIED_CLOCK','settlement_records':[]})
        cfg={'source_fingerprint':fingerprint(snapshot),'policy_fingerprint':p_hash,
             'scope':['A'],'windows':1,'start_at':AT}
        runfp=fingerprint(cfg)
        nextstate={'at':AFTER,'previous_schedule':[],'completed_rows':[],
                   'deferral_history':{},'cargo_progress':{},
                   'contract_accounting_state':{},'in_progress_contract_accounting':{}}
        d={'run_fingerprint':runfp,'window_id':1,'as_of_at':AT,
           'selected_schedule':[],'prior_state':None,'next_state':nextstate}
        prior=initial_rolling_state(AT,deferral_enabled=False)
        window={'window_id':1,'baseline_descriptor_v1':d,'prior_state_at_start':prior,
                'admission':{'due_call_ids':[],'admitted_call_ids':[],'deferred_call_ids':[]},
                'pilotage_gap_certificates':{},
                'state_at_boundary':vessel_statuses(snapshot,{'next_state':nextstate},{'A'})}
        return {'schema_version':BRIDGE_VERSION,'simulation_only':True,
           'operational_approval':False,'source_fingerprint':cfg['source_fingerprint'],
           'policy_fingerprint':p_hash,'run_fingerprint':runfp,
           'source_snapshot':snapshot,'rolling_manifest':{'run_fingerprint':runfp,'config':cfg},
           'pilotage_context':context,'operating_policy':policy,
           'daylight_source_sha256':sha,'daylight_source_base64':base64.b64encode(daylight).decode(),
           'pricing_policy':'CLEAR_WEATHER_BASELINE_V1','clock_policy':'BLOCK_UNVERIFIED_CLOCK',
           'settlement_records':[], 'windows':[window], 'all_requested_windows_validated':True}

    def test_valid_portable_attestation(self):
        self.assertEqual(verify_bundle_attestation(self.fixture_bundle())['status'],
                         'STRUCTURAL_ATTESTATION_PASS')

    def test_tampered_source_refused(self):
        x=self.fixture_bundle();x['source_snapshot']['calls'][0]['vessel_id']='CHANGED'
        with self.assertRaisesRegex(BridgeError,'Source attestation'):
            verify_bundle_attestation(x)

    def test_tampered_daylight_refused(self):
        x=self.fixture_bundle();x['daylight_source_base64']=base64.b64encode(b'OTHER').decode()
        with self.assertRaisesRegex(BridgeError,'Daylight source'):
            verify_bundle_attestation(x)

    def test_tampered_policy_refused(self):
        x=self.fixture_bundle();x['operating_policy']['schema_version']='OTHER'
        with self.assertRaisesRegex(BridgeError,'Policy attestation'):
            verify_bundle_attestation(x)

    def test_hidden_deferred_call_refused(self):
        x=self.fixture_bundle();x['windows'][0]['admission']['deferred_call_ids']=['A']
        with self.assertRaisesRegex(BridgeError,'admission'):
            verify_bundle_attestation(x)

    def test_false_completed_claim_refused(self):
        x=self.fixture_bundle();x['all_requested_windows_validated']=False
        with self.assertRaisesRegex(BridgeError,'completion'):
            verify_bundle_attestation(x)

    def test_false_realtime_status_refused(self):
        x=self.fixture_bundle();x['windows'][0]['state_at_boundary'][0]['status_origin']='ACTUAL'
        with self.assertRaisesRegex(BridgeError,'status projection|real-time'):
            verify_bundle_attestation(x)

if __name__=='__main__':unittest.main()
