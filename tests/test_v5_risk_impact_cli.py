"""Isolated CLI wiring tests, with no optimizer or network dependencies."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_v5_risk_impact_v1 import fixture, scenario, event
from scripts.v5_risk import run_risk_impact_v1 as cli


class RiskCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.source=self.base/'input';self.source.mkdir()
        self.run=self.base/'runs'/'v5-test123';self.run.mkdir(parents=True)
        self.day=self.base/'daylight.csv';self.day.write_text('valid test daylight')
        self.out=self.base/'new-output'
        self.scenario=self.base/'shock.json'
        self.bundle,self.audit=fixture()
        self.config={'data_dir':self.source,'pilotage_context':{'daylight_path':str(self.day)}}
        self.manifest=self.bundle['rolling_manifest']
        self.checkpoints=[{'window_id':1}]

    def run_with(self, sc, native=None):
        self.scenario.write_text(json.dumps(sc))
        with patch.object(cli,'load_execution_config',return_value=self.config),\
             patch.object(cli,'load_checkpoints',return_value=(self.manifest,self.checkpoints)),\
             patch.object(cli,'preflight_sources',return_value=(self.bundle['source_snapshot'],{'status':'PASS'})),\
             patch.object(cli,'build_bundle',return_value=self.bundle),\
             patch.object(cli,'verify_run_no_shock',return_value=self.audit if native is None else native):
            return cli.main(['--config',str(self.base/'cfg.json'),
                     '--run-directory',str(self.run),
                     '--scenario-file',str(self.scenario),
                     '--output-directory',str(self.out)])

    def test_empty_shocks_creates_two_new_evidence_files(self):
        self.assertEqual(self.run_with(scenario()),0)
        risk=json.loads((self.out/'risk_impact_v1.json').read_text())
        audit=json.loads((self.out/'native_no_shock_evidence.json').read_text())
        self.assertEqual(risk['status'],'NO_SHOCK_NATIVE_PARITY_ONLY')
        self.assertEqual(audit['status'],'NATIVE_NO_SHOCK_BASELINE_PARITY_PASS')
        self.assertFalse(risk['operational_approval'])

    def test_shock_report_does_not_claim_new_cost(self):
        self.assertEqual(self.run_with(scenario(event('ETA_DELAY',call_id='A',delay_seconds=3600))),0)
        report=json.loads((self.out/'risk_impact_v1.json').read_text())
        self.assertIsNone(report['scenario_demurrage_cents'])
        self.assertEqual(report['simulated_shocked_inventory'],'NOT_RUN')
        self.assertEqual(report['run_id'],'v5-test123')

    def test_native_no_shock_block_prevents_output(self):
        self.assertEqual(self.run_with(scenario(),native={'status':'BLOCKED'}),2)
        self.assertFalse(self.out.exists())

    def test_new_output_only(self):
        self.assertEqual(self.run_with(scenario()),0)
        self.assertEqual(self.run_with(scenario()),2)

    def test_partial_run_rejected(self):
        self.manifest['config']['windows']=2
        self.assertEqual(self.run_with(scenario()),2)
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
