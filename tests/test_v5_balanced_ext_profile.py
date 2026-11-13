"""Add to the user's repository. Needs no fixture data to check profile wiring.

For end-to-end tests set DOCKTIMIZE_EXT_INPUT_DIR to the separate two-JSON folder.
"""
import copy
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

from src.daily_planning.integrated_input.loader import (
    BALANCED_EXT_PROFILE, BALANCED_EXT_FILES, SCENARIO_FILES, load_inputs)
from src.daily_planning.integrated_input.normalizer import normalize
from src.daily_planning.integrated_input.v5 import validate_v5
from src.daily_planning.integrated_v3.v5_physical import _verified_snapshot
from src.daily_planning.integrated_v3.v5_scale import bounded_parameters
from scripts.v5_stage1.run_integrated_gates import scenario_order


class ExtendedProfileTests(unittest.TestCase):
    def test_extended_source_profile_is_explicit(self):
        self.assertEqual(SCENARIO_FILES[BALANCED_EXT_PROFILE], BALANCED_EXT_FILES)
        self.assertEqual(len(BALANCED_EXT_FILES), 2)
        self.assertTrue(BALANCED_EXT_FILES[1].endswith('BALANCED_20270109.json'))

    def test_validated_extended_input_and_negative_hash_case(self):
        root=os.getenv('DOCKTIMIZE_EXT_INPUT_DIR')
        if not root:self.skipTest('Extended fixture directory not supplied')
        source_dir=Path(root)
        bundle=load_inputs(source_dir,scenario=BALANCED_EXT_PROFILE)
        self.assertEqual(bundle['scenario_id'],'V5_BALANCED_154')
        self.assertEqual(bundle['source_profile_id'],BALANCED_EXT_PROFILE)
        snapshot=normalize(bundle)
        self.assertEqual(snapshot['source_profile_id'],BALANCED_EXT_PROFILE)
        result=validate_v5(snapshot)
        self.assertTrue(result['data_preflight_pass'],result.get('errors'))
        self.assertEqual(result['flow_record_count'],816)
        self.assertEqual(result['hard_fix_count'],15)
        self.assertEqual(result['source_profile_id'],BALANCED_EXT_PROFILE)
        self.assertEqual(_verified_snapshot(snapshot,source_dir),snapshot)
        wrong_profile=copy.deepcopy(snapshot)
        wrong_profile['source_profile_id']='V5_BALANCED_154'
        with self.assertRaises(ValueError):
            _verified_snapshot(wrong_profile,source_dir)
        tampered=copy.deepcopy(snapshot)
        artifact=next(a for a in tampered['artifacts'] if a['logical_filename'].endswith('_20270109.json'))
        artifact['sha256']='0'*64
        denied=validate_v5(tampered)
        self.assertFalse(denied['data_preflight_pass'])
        self.assertTrue(any('SHA mismatch' in x['message'] for x in denied['errors']))
        tampered=copy.deepcopy(snapshot)
        tampered['fixed_process_flows']['segments'].pop()
        denied=validate_v5(tampered)
        self.assertFalse(denied['data_preflight_pass'])
        tampered=copy.deepcopy(snapshot)
        tampered['fixed_process_flows']['segments'][0]['end_at']='2026-11-03T05:00:00+09:00'
        self.assertFalse(validate_v5(tampered)['data_preflight_pass'])
        tampered=copy.deepcopy(snapshot)
        tampered['source_input']['balanced_enrichment_provenance']['external_flow_json_sha256']='0'*64
        self.assertFalse(validate_v5(tampered)['data_preflight_pass'])

    def test_extended_scope_uses_balanced_gate(self):
        self.assertIn('FULL154',scenario_order({'scenario':BALANCED_EXT_PROFILE}))
        self.assertNotIn('FULL140',scenario_order({'scenario':BALANCED_EXT_PROFILE}))
        bounded_parameters(count=154,windows=60,time_limit_seconds=30,candidates_per_pair=6,
                           total_candidates=240,improvement_rounds=2,scenario=BALANCED_EXT_PROFILE)

    def test_run_gates_cli_imports_when_invoked_by_path(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([sys.executable,str(root/'scripts/v5_full/run_gates.py'),'--help'],
                              cwd=root,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('FULL154',result.stdout)

if __name__=='__main__':unittest.main()
