"""Run the isolated synthetic A/B reoptimization example with real CP-SAT."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from tests import test_v5_inventory_reoptimization as case
from src.daily_planning.integrated_v3.v5_physical import schedule_v5


def main():
    captured=[]
    def wrapper(*args,**kwargs):
        r=schedule_v5(*args,**kwargs);captured.append(r);return r
    suite=unittest.TestSuite([case.RealSolverReoptimizationTest('test_invalid_then_valid_with_fixed_call_and_exact_nogood')])
    with patch.object(case,'schedule_v5',side_effect=wrapper):result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or not captured:return 1
    r=captured[0];out=ROOT/'results/v5_inventory_reoptimization/synthetic_example.json';out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({'scenario_kind':'ISOLATED_SYNTHETIC_TEST','result':r},ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(out),'scenario_kind':'ISOLATED_SYNTHETIC_TEST','status':r['status'],'solver_attempt_count':r['solver_attempt_count'],'forbidden_schedule_count':r['forbidden_schedule_count'],'runtime_seconds':r['runtime_seconds'],'attempts':[{'attempt':a['attempt'],'etbs':a['selected_etbs'],'physical':a['physical_validation']['status'],'inventory':a['inventory_validation_status'],'violations':a['inventory_violations']} for a in r['reoptimization_attempts']]},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':sys.exit(main())
