"""Run isolated synthetic and supplied V5 subset as separate projections."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from tests.test_v5_rolling import rolling_fixture,START
from tests import test_v5_adaptive_candidates as adaptive
from src.daily_planning.integrated_v3.v5_physical import schedule_v5
from src.daily_planning.integrated_v3.v5_rolling import run_v5_rolling


def main():
    root=ROOT/'results/v5_rolling_runs';root.mkdir(parents=True,exist_ok=True);folder=Path(tempfile.mkdtemp(prefix='run-',dir=root));s,p,ctx=rolling_fixture()
    with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *a:copy.deepcopy(s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}):
        synthetic=run_v5_rolling(s,data_dir='ISOLATED_SYNTHETIC_TEST',policy=p,pilotage_context=ctx,as_of_at=START,output_dir=folder/'synthetic',windows=3,time_limit_seconds=30,max_cost_candidates=5,pricing_policy='CLEAR_WEATHER_BASELINE_V1')
    captured=[]
    def capture(*args,**kw):
        value=schedule_v5(*args,**kw);captured.append((args,kw));return value
    with patch.object(adaptive,'schedule_v5',side_effect=capture):adaptive.RealAdaptiveTest('test_actual_subset_automatic').test_actual_subset_automatic()
    if not captured:raise RuntimeError('Actual V5 inputs unavailable')
    args,kw=captured[0];real=run_v5_rolling(*args,data_dir=kw['data_dir'],policy=kw['policy'],pilotage_context=kw['pilotage_context'],as_of_at=kw['as_of_at'],output_dir=folder/'actual_v5',call_ids=kw['call_ids'],windows=3,time_limit_seconds=30,max_cost_candidates=5,pricing_policy='CLEAR_WEATHER_BASELINE_V1')
    report={'synthetic':synthetic,'actual_v5_simulation_only':real};path=folder/'summary.json';path.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    for name,r in report.items():print(name,json.dumps({'status':r['status'],'validated_window_count':r['validated_window_count'],'completed_call_ids':r['completed_call_ids'],'window_demurrage_results':r['window_demurrage_results'],'cumulative_demurrage_cents':r['cumulative_demurrage_cents'],'carried_inventory':r['carried_inventory'],'runtime_seconds':r['runtime_seconds'],'stopping_reason':r['stopping_reason'],'candidate_counts':[x['cost_candidates_evaluated'] for x in r['window_results']]},ensure_ascii=False))
    print('report',path);return 0 if synthetic['status']==real['status']=='REQUESTED_WINDOWS_VALIDATED' else 1
if __name__=='__main__':sys.exit(main())
