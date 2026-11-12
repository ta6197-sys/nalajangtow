import json
import tempfile
import unittest
from pathlib import Path
from src.daily_planning.integrated_v3.v5_scale import (
    ScaleInputError, atomic_json, bounded_parameters,
    load_execution_config, summarize_result, execute_scale)


class ScaleTest(unittest.TestCase):
    def test_runner_receives_empty_checkpoint_directory(self):
        from tests.test_v5_rolling import rolling_fixture
        snapshot,policy,context=rolling_fixture()
        config={'data_dir':'unused','policy':policy,'pilotage_context':context,'as_of_at':'2026-11-03T00:00:00+09:00','pricing_policy':'CLEAR_WEATHER_BASELINE_V1','clock_policy':'BLOCK_UNVERIFIED_CLOCK','settlement_records':[]}
        def runner(snapshot,**kwargs):
            self.assertEqual(list(kwargs['output_dir'].iterdir()),[])
            return {'status':'REQUESTED_WINDOWS_VALIDATED','window_results':[],'validated_window_count':0,'completed_call_ids':[]}
        with tempfile.TemporaryDirectory() as root:
            r=execute_scale(config,output_root=root,call_count=3,windows=1,runner=runner,snapshot=snapshot)
            self.assertEqual(r['status'],'REQUESTED_WINDOWS_VALIDATED',r)
            self.assertTrue((Path(root)/r['run_id']/'tanks_public.json').exists())
    def test_valid_small_bounds(self):
        bounded_parameters(count=5,windows=3,time_limit_seconds=30,candidates_per_pair=6,
                           total_candidates=240,improvement_rounds=2)

    def test_reject_invalid_and_unbounded_config(self):
        params=dict(count=5,windows=3,time_limit_seconds=30,
                    candidates_per_pair=6,total_candidates=240,improvement_rounds=2)
        for key,value in [('count',141),('windows',90),('time_limit_seconds',0),
                          ('time_limit_seconds',float('nan')),('candidates_per_pair',48),
                          ('total_candidates',20000),('improvement_rounds',3),('count',True)]:
            with self.subTest(key=key,value=value),self.assertRaises(ScaleInputError):
                bounded_parameters(**dict(params,**{key:value}))

    def test_overlapping_costs_never_summed(self):
        report={'status':'REQUESTED_WINDOWS_VALIDATED','window_results':[
          {'window_id':1,'window_demurrage_cents':300,'selected_call_ids':['S1'],
           'solver_status':'FEASIBLE'},
          {'window_id':2,'window_demurrage_cents':400,'selected_call_ids':['S1'],
           'solver_status':'OPTIMAL'}],
          'validated_window_count':2,'completed_call_ids':['S1'],
          'cumulative_demurrage_cents':100,'unresolved_accounting_call_ids':[]}
        result=summarize_result(report,['S1'],seconds=1.5)
        self.assertEqual(result['cumulative_demurrage_cents'],100)
        self.assertEqual(result['window_projected_costs_cents_NOT_ADDITIVE'],[300,400])
        self.assertFalse(result['full_period_global_optimality_proven'])

    def test_config_requires_real_inputs(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'config.json'
            path.write_text(json.dumps({'version':'V5_STAGED_EXECUTION_V1',
              'data_dir':'./missing','policy_file':'./missing_policy',
              'pilotage_context_file':'./missing_ctx','as_of_at':'2026-11-03T00:00:00+09:00'}))
            with self.assertRaisesRegex(ScaleInputError,'unavailable'):
                load_execution_config(path)

    def test_atomic_write_never_overwrites(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'foo.json'
            atomic_json(path, {'value':1})
            with self.assertRaises(FileExistsError): atomic_json(path,{'value':2})
            self.assertEqual(json.loads(path.read_text()),{'value':1})

if __name__ == '__main__':unittest.main()
