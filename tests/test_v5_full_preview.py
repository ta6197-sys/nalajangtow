import json
import tempfile
import unittest
from pathlib import Path
from src.daily_planning.integrated_v3.v5_preview_api import PreviewManager


class PreviewTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.registry=root/'registry.json';
        self.registry.write_text(json.dumps({'scenarios':{'demo':{'execution_config':'config.json'}}}))
        self.manager=PreviewManager(root/'runs',str(self.registry))

    def test_allowlist(self):
        self.assertEqual(self.manager.scenario_names(),['demo'])
        for body in ({'scenario':'nothere','vessels':3,'windows':2},
                     {'scenario':'demo','vessels':140,'windows':3},
                     {'scenario':'demo','vessels':3,'windows':14},
                     {'scenario':'demo','vessels':True,'windows':2},
                     {'scenario':'demo','vessels':3,'windows':2,'path':'../secret'}):
            with self.subTest(body=body):self.assertEqual(self.manager.start(body)[0],400)

    def test_active_job_excludes_concurrency(self):
        self.manager.active_run_id='v5-running'
        status,body=self.manager.start({'scenario':'demo','vessels':3,'windows':2})
        self.assertEqual(status,409)
        self.assertEqual(body['run_id'],'v5-running')

    def test_unconfigured_no_execution(self):
        self.assertEqual(PreviewManager(Path(self.temp.name)/'runs',None).scenario_names(),[])

if __name__=='__main__':unittest.main()
