"""Stage 2B integration: independent replay and bounded admission behavior."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_v5_rolling import rolling_fixture, START
from src.daily_planning.integrated_v3.v5_rolling import run_v5_rolling
from src.daily_planning.integrated_v3.v5_deferral import DEFERRAL_VERSION
from src.daily_planning.integrated_v3.v5_inventory_policy import POLICY_VERSION
from src.daily_planning.integrated_v3.v5_inventory_risk import RISK_VERSION
from src.daily_planning.integrated_v3.v5_rolling_optimizer import optimize_integrated_window


class RollingAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.snapshot,self.policy,self.context=rolling_fixture()
        self.snapshot['scenario_id']='V5_BALANCED_154'
        for tank in self.snapshot['tanks']:
            tank['effective_max_inventory_bbl']=str(int(tank['capacity_bbl'])*95//100)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)/'run'

    def run_rolling(self, **kwargs):
        args=dict(data_dir='SYNTHETIC',policy=self.policy,pilotage_context=self.context,
                  as_of_at=START,output_dir=self.folder,windows=2,time_limit_seconds=30,
                  pricing_policy='CLEAR_WEATHER_BASELINE_V1',rolling_optimizer_mode='INTEGRATED_COST_AWARE_V1',
                  inventory_policy_version=POLICY_VERSION,inventory_risk_policy_version=RISK_VERSION,
                  deferral_policy_version=DEFERRAL_VERSION)
        args.update(kwargs)
        with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *a:copy.deepcopy(self.snapshot)), \
             patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}):
            return run_v5_rolling(self.snapshot,**args)

    def test_verified_subset_and_resume_history(self):
        attempts=[]
        def optimizer(snapshot,work,**kwargs):
            attempts.append(list(kwargs['call_ids']))
            if len(attempts)==1:
                return {'status':'WINDOW_NOT_VALIDATED','reason':'INFEASIBLE','issues':[],
                        'optimizer_result':{'solver_status':'INFEASIBLE','issues':[]}}
            return optimize_integrated_window(snapshot,work,**kwargs)
        with patch('src.daily_planning.integrated_v3.v5_rolling_optimizer.optimize_integrated_window',side_effect=optimizer):
            first=self.run_rolling(max_new_windows=1)
        self.assertEqual(first['status'],'PAUSED',first)
        cp=json.loads((self.folder/'window_001.json').read_text())
        self.assertEqual(len(cp['deferred_call_ids']),1)
        deferred=cp['deferred_call_ids'][0]
        self.assertEqual(cp['deferral_history'][deferred]['carryover_count'],1)
        self.assertEqual(cp['summary']['cost_scope'],'ADMITTED_ONLY_DEFERRED_LIABILITIES_UNPRICED')
        self.assertEqual(cp['summary']['inventory_risk_v1']['risk_affects_feasibility'],False)
        self.assertGreaterEqual(len(attempts),2)
        second=self.run_rolling(resume=True)
        self.assertEqual(second['status'],'REQUESTED_WINDOWS_VALIDATED',second)
        self.assertEqual(second['validated_window_count'],2)
        self.assertIn(deferred,second['deferral_history'])

    def test_timeout_does_not_defer_or_checkpoint(self):
        failure={'status':'WINDOW_NOT_VALIDATED','reason':'TIME_LIMIT_NO_INCUMBENT','issues':[],
                 'optimizer_result':{'solver_status':'UNKNOWN','issues':[]}}
        with patch('src.daily_planning.integrated_v3.v5_rolling_optimizer.optimize_integrated_window',return_value=failure) as run:
            result=self.run_rolling()
        self.assertEqual(run.call_count,1)
        self.assertEqual(result['validated_window_count'],0)
        self.assertFalse((self.folder/'window_001.json').exists())

    def test_tampered_history_rejected_even_with_recomputed_file_hash(self):
        from src.daily_planning.integrated_v3.v5_rolling import _fingerprint
        first=self.run_rolling(max_new_windows=1)
        self.assertEqual(first['status'],'PAUSED',first)
        path=self.folder/'window_001.json';cp=json.loads(path.read_text())
        call=next(iter(cp['deferral_history']))
        cp['deferral_history'][call]['carryover_count']+=1
        cp.pop('checkpoint_fingerprint');cp['checkpoint_fingerprint']=_fingerprint(cp)
        path.write_text(json.dumps(cp))
        with self.assertRaisesRegex(ValueError,'history mismatch'):
            self.run_rolling(resume=True)

    def test_risk_tampering_is_rejected_on_resume(self):
        from src.daily_planning.integrated_v3.v5_rolling import _fingerprint
        self.assertEqual(self.run_rolling(max_new_windows=1)['status'],'PAUSED')
        path=self.folder/'window_001.json';cp=json.loads(path.read_text())
        cp['summary']['inventory_risk_v1']['risk_affects_objective']=True
        cp.pop('checkpoint_fingerprint');cp['checkpoint_fingerprint']=_fingerprint(cp)
        path.write_text(json.dumps(cp))
        with self.assertRaisesRegex(ValueError,'Risk checkpoint/replay mismatch'):
            self.run_rolling(resume=True)


if __name__=='__main__': unittest.main()
