"""Stdlib regression tests for V5 risk impact screening (no solver)."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import unittest

from src.daily_planning.integrated_v3.v5_simulation_bridge_v2 import (
    fingerprint as v2fp, initial_rolling_state, vessel_statuses,
    verify_bundle_attestation, BRIDGE_VERSION,
)
from src.daily_planning.integrated_v3.v5_risk_impact_v1 import (
    validate_scenario, assess, RiskImpactError, SCENARIO_VERSION,
    _fraction, _work_seconds, _overlap_seconds,
)

AT = '2026-11-03T00:00:00+09:00'
NEXT = '2026-11-04T00:00:00+09:00'


def fixture():
    call={'call_id':'A','vessel_id':'V-A','eta_at':'2026-11-03T08:00:00+09:00',
          'fixed_etb_is_hard':False,'cargo_volume_bbl':'100'}
    source={'calls':[call], 'berths':[{'berth_id':'BERTH-01'}], 'tanks':[]}
    pol={'schema_version':'SYNTHETIC_UNIT_FIXTURE'}
    context={'daylight_path':'src/daily_planning/daylight_windows.csv'}
    daylight=b'date_kst,sunrise_kst,sunset_kst\n2026-11-03,06:44,17:28\n'
    day_sha=sha256(daylight).hexdigest()
    policy_sha=v2fp({'policy':pol,'pilotage_context':context,'daylight_sha256':day_sha,
                     'pricing_policy':'CLEAR_WEATHER_BASELINE_V1',
                     'clock_policy':'BLOCK_UNVERIFIED_CLOCK','settlement_records':[]})
    cfg={'start_at':AT,'scope':['A'],'windows':1,'source_fingerprint':v2fp(source),
         'policy_fingerprint':policy_sha}
    run_fp=v2fp(cfg)
    row={'call_id':'A','berth_id':'BERTH-01','pob_at':'2026-11-03T09:00:00+09:00',
         'etb_at':'2026-11-03T10:00:00+09:00',
         'work_start_at':'2026-11-03T12:00:00+09:00',
         'work_end_at':'2026-11-03T14:00:00+09:00',
         'etu_at':'2026-11-03T16:00:00+09:00',
         'work_intervals':[['2026-11-03T12:00:00+09:00', '2026-11-03T14:00:00+09:00']]}
    after={'at':NEXT,'completed_rows':[{'call_id':'A'}], 'previous_schedule':[],
           'deferral_history':{},'cargo_progress':{},'contract_accounting_state':{},
           'in_progress_contract_accounting':{}}
    descriptor={'run_fingerprint':run_fp,'window_id':1,'as_of_at':AT,
                'planning_horizon_end':'2026-11-06T00:00:00+09:00',
                'selected_schedule':[row], 'prior_state':None,'next_state':after,
                'window_projected_demurrage_cents':100}
    cp={'next_state':after}
    window={'window_id':1,'as_of_at':AT,'baseline_descriptor_v1':descriptor,
            'prior_state_at_start':initial_rolling_state(AT,deferral_enabled=False),
            'admission':{'due_call_ids':['A'],'admitted_call_ids':['A'],
                         'deferred_call_ids':[]},
            'pilotage_gap_certificates':{},
            'state_at_boundary':vessel_statuses(source,cp,{'A'})}
    bundle={'schema_version':BRIDGE_VERSION,'simulation_only':True,
            'operational_approval':False,'source_fingerprint':cfg['source_fingerprint'],
            'policy_fingerprint':policy_sha,'run_fingerprint':run_fp,
            'source_snapshot':source,'operating_policy':pol,'pilotage_context':context,
            'daylight_source_sha256':day_sha,
            'daylight_source_base64':base64.b64encode(daylight).decode('ascii'),
            'pricing_policy':'CLEAR_WEATHER_BASELINE_V1',
            'clock_policy':'BLOCK_UNVERIFIED_CLOCK','settlement_records':[],
            'rolling_manifest':{'run_fingerprint':run_fp,'config':cfg},
            'windows':[window],'all_requested_windows_validated':True}
    audit={'status':'NATIVE_NO_SHOCK_BASELINE_PARITY_PASS',
           'native_resume_replay':'PASS','validated_windows':1,
           'window_parity':[{'status':'PHYSICAL_INVENTORY_SETTLEMENT_PARITY_PASS',
                            'window_id':1,'demurrage_cents':100,'schedule_row_count':1}]}
    assert verify_bundle_attestation(bundle)['status']=='STRUCTURAL_ATTESTATION_PASS'
    return bundle,audit


def event(kind, **kwargs):
    return dict(shock_id='test1',type=kind,**kwargs)


def scenario(*shocks):
    return dict(version=SCENARIO_VERSION,scenario_id='TEST',shock_events=list(shocks))


class RiskImpactTests(unittest.TestCase):
    def setUp(self):
        self.bundle,self.audit=fixture()

    def run_it(self, *events):
        return assess(self.bundle,scenario(*events),baseline_audit=self.audit)

    def test_no_shock_is_not_new_stochastic_simulation(self):
        r=self.run_it()
        self.assertEqual(r['status'],'NO_SHOCK_NATIVE_PARITY_ONLY')
        self.assertIsNone(r['scenario_demurrage_delta_cents'])
        self.assertEqual(r['baseline_window_projected_demurrage_cents_NOT_ADDITIVE'][0]['cost_cents'],100)
        self.assertEqual(r['simulated_shocked_inventory'],'NOT_RUN')

    def test_eta_delay_beyond_pob(self):
        r=self.run_it(event('ETA_DELAY',call_id='A',delay_seconds=7200))
        hit=r['shock_events'][0]['evidence'][0]
        self.assertEqual(hit['arrival_shortfall_seconds'],3600)
        self.assertEqual(hit['verdict'],'ETA_AFTER_POB_REPLAN_REQUIRED')
        self.assertEqual(r['affected_call_ids'],['A'])
        self.assertEqual(r['affected_window_ids'],[1])
        self.assertIsNone(r['scenario_demurrage_cents'])

    def test_eta_within_slack_does_not_claim_cost_replay(self):
        r=self.run_it(event('ETA_DELAY',call_id='A',delay_seconds=1800))
        self.assertEqual(r['shock_events'][0]['evidence'][0]['verdict'],
                         'ETA_REMAINS_BEFORE_POB_FIXED_ROW_ONLY')
        self.assertEqual(r['status'],'SHOCK_IMPACT_SCREEN_ONLY_REPLAY_NOT_RUN')

    def test_work_slowdown_exact_seconds(self):
        r=self.run_it(event('WORK_DURATION_MULTIPLIER',call_id='A',multiplier='3/2'))
        h=r['shock_events'][0]['evidence'][0]
        self.assertEqual(h['baseline_work_seconds'],7200)
        self.assertEqual(h['minimum_required_work_seconds'],10800)
        self.assertEqual(h['extra_work_seconds_minimum'],3600)
        self.assertEqual(h['verdict'],'DURATION_RATE_AND_WORK_WINDOW_REPLAY_REQUIRED')

    def test_weather_overlap_exact_seconds(self):
        r=self.run_it(event('WEATHER_CARGO_SHUTDOWN',start_at='2026-11-03T12:30:00+09:00',
                         end_at='2026-11-03T13:30:00+09:00',berth_ids=['BERTH-01']))
        self.assertEqual(r['shock_events'][0]['evidence'][0]['baseline_work_seconds_in_shutdown'],3600)
        self.assertEqual(r['affected_call_ids'],['A'])

    def test_weather_nonoverlap_still_not_risk_free_certificate(self):
        r=self.run_it(event('WEATHER_CARGO_SHUTDOWN',start_at='2026-11-04T07:00:00+09:00',
                           end_at='2026-11-04T08:00:00+09:00',berth_ids=['BERTH-01']))
        self.assertEqual(r['shock_events'][0]['evidence'],[])
        self.assertEqual(r['status'],'SHOCK_IMPACT_SCREEN_ONLY_REPLAY_NOT_RUN')
        self.assertIsNone(r['scenario_demurrage_cents'])

    def test_multiple_shocks_have_distinct_attribution(self):
        r=self.run_it(event('ETA_DELAY',call_id='A',delay_seconds=7200),
            dict(shock_id='work2',type='WORK_DURATION_MULTIPLIER',call_id='A',multiplier='6/5'))
        self.assertEqual(len(r['shock_events']),2)
        self.assertEqual(r['affected_call_ids'],['A'])

    def test_missing_native_baseline_is_blocked(self):
        with self.assertRaisesRegex(RiskImpactError,'NO_SHOCK'):
            assess(self.bundle,scenario(),baseline_audit={'status':'NOT_RUN'})

    def test_partial_native_baseline_is_blocked(self):
        self.audit['window_parity']=[]
        with self.assertRaisesRegex(RiskImpactError,'window parity'):
            self.run_it()

    def test_baseline_from_different_window_is_blocked(self):
        self.audit['window_parity'][0]['demurrage_cents']=99
        with self.assertRaisesRegex(RiskImpactError,'saved window'):
            self.run_it()

    def test_modified_source_is_rejected(self):
        self.bundle['source_snapshot']['calls'][0]['vessel_id']='CORRUPTED'
        with self.assertRaisesRegex(ValueError,'Source attestation'):
            self.run_it()

    def test_incomplete_windows_rejected(self):
        self.bundle['all_requested_windows_validated']=False
        with self.assertRaisesRegex(ValueError,'completion'):
            self.run_it()

    def test_unknown_call_is_rejected(self):
        with self.assertRaises(RiskImpactError):
            self.run_it(event('ETA_DELAY',call_id='UNKNOWN',delay_seconds=12))

    def test_unknown_berth_rejected(self):
        with self.assertRaisesRegex(RiskImpactError,'berth'):
            self.run_it(event('WEATHER_CARGO_SHUTDOWN',start_at='2026-11-03T13:00:00+09:00',
                end_at='2026-11-03T14:00:00+09:00',berth_ids=['UNKNOWN']))

    def test_duplicate_shock_ids_rejected(self):
        with self.assertRaisesRegex(RiskImpactError,'shock ID'):
            self.run_it(event('ETA_DELAY',call_id='A',delay_seconds=12),
                event('ETA_DELAY',call_id='A',delay_seconds=22))

    def test_malformed_ratio_rejected(self):
        for ratio in (True,1.2,'1','0','5','1/0','Infinity','9'*60):
            with self.subTest(value=ratio):
                with self.assertRaises(RiskImpactError):
                    self.run_it(event('WORK_DURATION_MULTIPLIER',call_id='A',multiplier=ratio))

    def test_noninteger_delay_rejected(self):
        for value in (True,0,-1,1.2,'1800'):
            with self.subTest(value=value):
                with self.assertRaises(RiskImpactError):
                    self.run_it(event('ETA_DELAY',call_id='A',delay_seconds=value))

    def test_unoffset_weather_rejected(self):
        with self.assertRaisesRegex(RiskImpactError,'offset'):
            self.run_it(event('WEATHER_CARGO_SHUTDOWN',start_at='2026-11-03T13:00:00',
                end_at='2026-11-03T14:00:00+09:00',berth_ids=['BERTH-01']))

    def test_end_before_start_weather_rejected(self):
        with self.assertRaises(RiskImpactError):
            self.run_it(event('WEATHER_CARGO_SHUTDOWN',start_at='2026-11-04T13:00:00+09:00',
                end_at='2026-11-03T14:00:00+09:00',berth_ids=['BERTH-01']))

    def test_forbidden_fields_fail_closed(self):
        with self.assertRaisesRegex(RiskImpactError,'Unsupported'):
            self.run_it(dict(shock_id='test1',type='ETA_DELAY',call_id='A',delay_seconds=3600,
                             fixed_etb_override='2026-11-04T00:00:00+09:00'))

    def test_physical_work_intervals_must_be_consistent(self):
        row={'work_start_at':'2026-11-03T12:00:00+09:00',
             'work_end_at':'2026-11-03T15:00:00+09:00',
             'work_intervals':[['2026-11-03T12:00:00+09:00','2026-11-03T14:00:00+09:00']]}
        with self.assertRaisesRegex(RiskImpactError,'endpoints'):
            _work_seconds(row)

    def test_intersection_exclusive_boundary(self):
        self.assertEqual(_overlap_seconds('2026-11-03T12:00:00+09:00','2026-11-03T13:00:00+09:00',
                  '2026-11-03T13:00:00+09:00','2026-11-03T14:00:00+09:00'),0)

    def test_fraction_boundary(self):
        self.assertEqual(_fraction('7/6','ratio').numerator,7)

    def test_only_50_shocks(self):
        xs=[dict(shock_id='s'+str(i),type='ETA_DELAY',call_id='A',delay_seconds=1) for i in range(51)]
        with self.assertRaises(RiskImpactError):
            self.run_it(*xs)


if __name__ == '__main__':
    unittest.main()
