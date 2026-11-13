"""Repository-only V5 integration smoke: real synthetic solver + no-shock replay.

Requires full Docktimize checkout, OR-Tools and existing test fixtures.
Must run in addition to the pure tests; not executable from standalone ZIP.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_v5_rolling import rolling_fixture, START
from src.daily_planning.integrated_v3.v5_rolling import run_v5_rolling
from src.daily_planning.integrated_v3.v5_simulation_bridge_v2 import (
    build_bundle, verify_bundle_attestation, verify_run_no_shock, BridgeError)
from src.daily_planning.integrated_v3.v5_run_review import load_checkpoints
from src.daily_planning.integrated_v3.v5_deferral import DEFERRAL_VERSION
from src.daily_planning.integrated_v3.v5_inventory_policy import POLICY_VERSION


class NativeNoShockIntegration(unittest.TestCase):
    def setUp(self):
        self.snapshot,self.policy,self.context=rolling_fixture()
        self.snapshot['scenario_id']='V5_BALANCED_154'
        for tank in self.snapshot['tanks']:
            tank['effective_max_inventory_bbl']=str(int(tank['capacity_bbl'])*95//100)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.run_dir=self.root/'run'
        self.config={
            'data_dir':'ISOLATED_SYNTHETIC_TEST','policy':self.policy,
            'pilotage_context':self.context,'as_of_at':START,
            'pricing_policy':'CLEAR_WEATHER_BASELINE_V1',
            'clock_policy':'BLOCK_UNVERIFIED_CLOCK','settlement_records':[],
            'inventory_policy_version':POLICY_VERSION,
            'deferral_policy_version':DEFERRAL_VERSION,'max_deferral_probes':8,
        }
        self.args={
            'data_dir':self.config['data_dir'],'policy':self.policy,
            'pilotage_context':self.context,'as_of_at':START,
            'output_dir':self.run_dir,'windows':2,'time_limit_seconds':30,
            'pricing_policy':self.config['pricing_policy'],
            'rolling_optimizer_mode':'INTEGRATED_COST_AWARE_V1',
            'inventory_policy_version':POLICY_VERSION,
            'deferral_policy_version':DEFERRAL_VERSION,
        }

    def source_mocks(self):
        # Used by repo's existing test fixture: do not use on real input.
        from contextlib import ExitStack
        stack=ExitStack()
        stack.enter_context(patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',
                           side_effect=lambda *args:copy.deepcopy(self.snapshot)))
        stack.enter_context(patch('src.daily_planning.integrated_input.v5.validate_v5',
                           return_value={'data_preflight_pass':True,'errors':[]}))
        return stack

    def test_native_no_shock_checkpoint_equality(self):
        with self.source_mocks():
            completed=run_v5_rolling(self.snapshot,**self.args)
            self.assertEqual(completed['status'],'REQUESTED_WINDOWS_VALIDATED',completed)
            manifest,cps=load_checkpoints(self.run_dir)
            daylight=Path(self.context['daylight_path']).read_bytes()
            bundle=build_bundle(self.snapshot,self.config,manifest,cps,daylight)
            self.assertEqual(verify_bundle_attestation(bundle)['status'],
                             'STRUCTURAL_ATTESTATION_PASS')
            verified=verify_run_no_shock(self.snapshot,self.config,manifest,cps,
                       run_directory=self.run_dir)
        self.assertEqual(verified['status'],'NATIVE_NO_SHOCK_BASELINE_PARITY_PASS',verified)
        self.assertEqual(verified['native_resume_replay'],'PASS')
        self.assertFalse(verified['operational_approval'])
        self.assertEqual(len(bundle['windows']),2)

    def test_changed_input_is_blocked_by_source_fingerprint(self):
        with self.source_mocks():
            completed=run_v5_rolling(self.snapshot,**self.args)
            self.assertEqual(completed['status'],'REQUESTED_WINDOWS_VALIDATED')
            manifest,cps=load_checkpoints(self.run_dir)
        altered=copy.deepcopy(self.snapshot)
        altered['calls'][0]['vessel_id']='TAMPERED'
        daylight=Path(self.context['daylight_path']).read_bytes()
        with self.assertRaisesRegex(BridgeError,'Source fingerprint'):
            build_bundle(altered,self.config,manifest,cps,daylight)

if __name__=='__main__':unittest.main()
