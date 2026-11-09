"""Documentation routes must never call planning or mutate the database."""
import ast
import hashlib
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

import web_app

ROOT=Path(__file__).resolve().parents[1]


class ApiDocsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),web_app.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.origin=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def test_docs_assets_and_spec_are_local_and_read_only(self):
        with patch.object(web_app,'current_state',side_effect=AssertionError('docs must not query DB')),patch.object(web_app,'plan_next',side_effect=AssertionError('docs must not plan')):
            for route,content_type in [('/docs','text/html'),('/docs/','text/html'),('/docs/init.js','text/javascript'),('/docs/swagger-ui.css','text/css'),('/docs/swagger-ui-bundle.js','text/javascript'),('/openapi.json','application/json')]:
                with self.subTest(route=route),urlopen(self.origin+route) as response:
                    self.assertEqual(response.status,200)
                    self.assertIn(content_type,response.headers['Content-Type'])
                    self.assertTrue(response.read())
            with urlopen(self.origin+'/openapi.json') as response:spec=json.load(response)
            self.assertEqual(spec['servers'],[{'url':'/','description':'이 Swagger 화면을 제공하는 현재 서버'}])
            html=(ROOT/'web/docs.html').read_text()
            self.assertNotIn('https://',html)
            init=(ROOT/'web/swagger-init.js').read_text()
            self.assertIn('validatorUrl:null',init)
            self.assertIn('tryItOutEnabled:false',init)

    def test_spec_covers_every_existing_post_route(self):
        spec=json.loads((ROOT/'api/openapi.json').read_text())
        tree=ast.parse((ROOT/'web_app.py').read_text())
        routes=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.FunctionDef) and node.name=='do_POST':
                for child in ast.walk(node):
                    if isinstance(child,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='actions' for t in child.targets):
                        routes={key.value for key in child.value.keys}
        self.assertTrue(routes)
        documented={path for path,methods in spec['paths'].items() if 'post' in methods}
        self.assertEqual(routes,documented)
        operation_ids=[op['operationId'] for methods in spec['paths'].values() for op in methods.values()]
        self.assertEqual(len(operation_ids),len(set(operation_ids)))
        self.assertEqual(len(spec['paths']),17)
        self.assertTrue(all('get' in spec['paths'][path] and 'post' not in spec['paths'][path]
            for path in ('/api/risk/analyses','/api/risk/analyses/{analysis_id}',
                         '/api/risk/analyses/{analysis_id}/scenarios/{scenario_id}')))

    def test_forecast_endpoint_is_read_only(self):
        with (patch.object(web_app, 'get_forecast_v1', return_value={'schema_version': 'FORECAST_V1', 'read_only': True}) as forecast,
              patch.object(web_app, 'plan_next', side_effect=AssertionError('forecast must not plan')),
              patch.object(web_app, 'confirm_candidate', side_effect=AssertionError('forecast must not confirm'))):
            with urlopen(self.origin + '/api/forecast/v1?run_id=00000000-0000-0000-0000-000000000001') as response:
                payload = json.loads(response.read())
                self.assertTrue(payload['read_only'])
            forecast.assert_called_once()
            with self.assertRaises(HTTPError) as exc:
                urlopen(self.origin + '/api/forecast/v1')
            self.assertEqual(exc.exception.code, 400)

    def test_unknown_assets_do_not_expose_other_files(self):
        for path in ('/docs/manifest.json','/docs/../.env','/docs/anything.js'):
            with self.subTest(path=path),self.assertRaises(HTTPError) as exc:urlopen(self.origin+path)
            self.assertEqual(exc.exception.code,404)

    def test_risk_get_routes_are_read_only_and_assets_are_local(self):
        for route in ('/assets/echarts.min.js','/assets/risk_charts.js','/assets/risk.css'):
            with urlopen(self.origin+route) as response:
                self.assertEqual(response.status,200)
                self.assertTrue(response.read())
        with (patch.object(web_app,'list_risk_analyses',return_value={'run_id':'x','analyses':[]}) as listed,
              patch.object(web_app,'get_risk_distribution_payload',return_value={'analysis_id':'x'}) as analysis,
              patch.object(web_app,'get_risk_scenario_payload',return_value={'scenario_id':'s'}) as scenario,
              patch.object(web_app,'plan_next',side_effect=AssertionError('Risk GET must not plan'))):
            paths=('/api/risk/analyses?run_id=00000000-0000-0000-0000-000000000001',
                   '/api/risk/analyses/00000000-0000-0000-0000-000000000002',
                   '/api/risk/analyses/00000000-0000-0000-0000-000000000002/scenarios/s')
            for route in paths:
                with urlopen(self.origin+route) as response:
                    self.assertEqual(response.status,200)
            listed.assert_called_once()
            analysis.assert_called_once()
            scenario.assert_called_once()

    def test_vendored_asset_integrity(self):
        root=ROOT/'web/vendor/swagger-ui'
        manifest=json.loads((root/'manifest.json').read_text())
        self.assertEqual(manifest['package'],'swagger-ui-dist')
        for name,digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),digest)
        echarts=ROOT/'web/vendor/echarts'
        manifest=json.loads((echarts/'manifest.json').read_text())
        self.assertEqual(manifest['package'],'echarts')
        self.assertEqual(hashlib.sha256((echarts/'echarts.min.js').read_bytes()).hexdigest(),
                         manifest['sha256'])


if __name__=='__main__':unittest.main()
