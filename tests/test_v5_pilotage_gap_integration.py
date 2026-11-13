"""Repository integration checks. Run after applying Stage 1 patch to real repo.

These tests use existing V5 fixtures and mock only the new proof adapter, so
Rolling transitions, checkpoint validation, and independent resume remain real.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_v5_rolling import rolling_fixture, START
from src.daily_planning.integrated_v3.v5_rolling import run_v5_rolling, _fingerprint
from src.daily_planning.integrated_v3.v5_deferral import DEFERRAL_VERSION
from src.daily_planning.integrated_v3.v5_inventory_policy import POLICY_VERSION
from src.daily_planning.integrated_v3.v5_pilotage_gap import PILOTAGE_GAP_VERSION


class PilotageGapRollingIntegration(unittest.TestCase):
    def setUp(self):
        self.snapshot,self.policy,self.context=rolling_fixture()
        self.snapshot['scenario_id']='V5_BALANCED_154'
        for tank in self.snapshot['tanks']:
            tank['effective_max_inventory_bbl']=str(int(tank['capacity_bbl'])*95//100)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.folder=Path(tmp.name)/'run'

    def fake_proof(self, work, call_ids, **kw):
        # Fixture-specific, synthetic adapter: first window SYN-3 is provably
        # outside the pilotage horizon, and becomes eligible again in window 2.
        if kw['as_of_at']==START and 'SYN-3' in call_ids:
            return {'certificates':{'SYN-3':{'certificate_version':PILOTAGE_GAP_VERSION,'call_id':'SYN-3'}},'unresolved':{}}
        return {'certificates':{},'unresolved':{}}

    def run_rolling(self, **kwargs):
        args=dict(data_dir='ISOLATED_SYNTHETIC_TEST',policy=self.policy,pilotage_context=self.context,
                  as_of_at=START,output_dir=self.folder,windows=2,time_limit_seconds=30,
                  pricing_policy='CLEAR_WEATHER_BASELINE_V1',
                  rolling_optimizer_mode='INTEGRATED_COST_AWARE_V1',
                  inventory_policy_version=POLICY_VERSION,
                  deferral_policy_version=DEFERRAL_VERSION,
                  horizon_pilotage_policy_version=PILOTAGE_GAP_VERSION)
        args.update(kwargs)
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *a:copy.deepcopy(self.snapshot)), \
             patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}), \
             patch('src.daily_planning.integrated_v3.v5_rolling.collect_certificates',side_effect=self.fake_proof):
            return run_v5_rolling(self.snapshot,**args)

    def test_proven_wait_reenters_next_window_and_replays(self):
        first=self.run_rolling(max_new_windows=1)
        self.assertEqual(first['status'],'PAUSED',first)
        cp=json.loads((self.folder/'window_001.json').read_text())
        self.assertIn('SYN-3',cp['deferred_call_ids'])
        self.assertNotIn('SYN-3',cp['admitted_call_ids'])
        self.assertEqual(cp['deferral_history']['SYN-3']['carryover_count'],1)
        self.assertEqual(cp['horizon_pilotage_policy_version'],PILOTAGE_GAP_VERSION)
        self.assertEqual(cp['summary']['cost_scope'],'ADMITTED_ONLY_DEFERRED_LIABILITIES_UNPRICED')
        second=self.run_rolling(resume=True)
        self.assertEqual(second['status'],'REQUESTED_WINDOWS_VALIDATED',second)
        self.assertEqual(second['validated_window_count'],2)
        cp2=json.loads((self.folder/'window_002.json').read_text())
        self.assertIn('SYN-3',cp2['admitted_call_ids'])
        self.assertEqual(cp2['horizon_pilotage_gap_certificates'],{})

    def test_tampered_certificate_fails_resume_with_recomputed_hash(self):
        self.assertEqual(self.run_rolling(max_new_windows=1)['status'],'PAUSED')
        path=self.folder/'window_001.json';cp=json.loads(path.read_text())
        cp['horizon_pilotage_gap_certificates']['SYN-3']['call_id']='FAKE'
        cp.pop('checkpoint_fingerprint');cp['checkpoint_fingerprint']=_fingerprint(cp)
        path.write_text(json.dumps(cp))
        with self.assertRaisesRegex(ValueError,'Pilotage gap checkpoint proof'):
            self.run_rolling(resume=True)

    def test_policy_requires_existing_safe_deferral(self):
        with self.assertRaisesRegex(ValueError,'Stage 2B'):
            self.run_rolling(deferral_policy_version=None)

if __name__=='__main__':unittest.main()
