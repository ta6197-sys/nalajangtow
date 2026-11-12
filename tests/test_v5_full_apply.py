import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from apply_integration import transform_web_app,transform_compose,transform_html


class ApplyTest(unittest.TestCase):
    def test_web_anchors_and_idempotence(self):
        source='''    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        static_files = {
    def do_POST(self):
        actions = {'/api/contract': add_contract, '/api/call': add_call,
'''
        s=transform_web_app(source)
        self.assertIn('/api/v5-preview/start',s)
        self.assertIn('/v5-preview',s)
        with self.assertRaises(ValueError):transform_web_app(s)

    def test_compose_anchored_patch(self):
        s='''      WEB_EXPERIMENT_KEY: source-daywork-v2
      - "0.0.0.0:8765:8765"
      - ./results/source-daywork-v2:/app/results/daily
'''
        fixed=transform_compose(s)
        self.assertIn('127.0.0.1:8765:8765',fixed)
        self.assertIn('v5_preview_runs',fixed)

    def test_html_nav(self):
        s='<div class="sidebar-bottom"><div class="small-muted">'
        self.assertIn('/v5-preview',transform_html(s))

if __name__=='__main__':unittest.main()
