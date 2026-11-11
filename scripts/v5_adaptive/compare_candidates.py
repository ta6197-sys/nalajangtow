"""Compare automatic and manual candidate modes on the same real V5 calls."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from tests import test_v5_adaptive_candidates as cases
from src.daily_planning.integrated_v3.v5_physical import schedule_v5


def main():
    captured=[]
    def capture(*args,**kwargs):
        result=schedule_v5(*args,**kwargs);captured.append((args,kwargs,result));return result
    with patch.object(cases,'schedule_v5',side_effect=capture):
        outcome=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(cases.RealAdaptiveTest))
    if not outcome.wasSuccessful() or outcome.skipped or not captured:return 1
    args,kwargs,_=captured[0];manual_args=dict(kwargs);manual_args['candidate_generation_mode']='MANUAL_CANDIDATES';manual_args['candidate_etbs']={'DEM-0112':['2026-11-04T10:00:00+09:00','2026-11-04T11:00:00+09:00']}
    manual=schedule_v5(*args,**manual_args);automatic=schedule_v5(*args,**kwargs)
    output={'scenario_id':'V5_3TANK','calls':['DEM-0103','DEM-0112'],'scope':'Same selected calls and synthetic policy; finite candidate domains differ; warmed process, one run per mode; no whole-schedule feasibility claim','manual':manual,'automatic':automatic}
    target=ROOT/'results/v5_adaptive_comparison/comparison.json';target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(output,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    for name,r in [('manual',manual),('automatic',automatic)]:
        p=r.get('physical_solver_result',{});print(name,{'status':r['status'],'candidate_count':r['final_candidate_count'], 'physical_options':p.get('option_count'),'rounds':len(r['generation_rounds']),'cp_sat_seconds':p.get('cp_sat_seconds'),'physical_stage_seconds':p.get('seconds'),'total_seconds':r['runtime_seconds'],'physical':r['physical_validation'],'inventory':r['inventory_validation']})
    print('report',target)
    return 0 if manual['status']==automatic['status']=='PHYSICAL_AND_INVENTORY_VALID' else 1
if __name__=='__main__':sys.exit(main())
