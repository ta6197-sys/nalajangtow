"""Run the user-specified synthetic policy against the actual V5 test subset."""
import argparse
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tests import test_v5_physical_integration as test_module
from src.daily_planning.integrated_v3.v5_physical import schedule_v5


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'results/v5_physical_integration/validation_result.json');args=parser.parse_args()
    collected=[]
    def capture(*args,**kwargs):
        result=schedule_v5(*args,**kwargs);collected.append(result);return result
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(test_module.V5RealIntegrationTest)
    with patch.object(test_module,'schedule_v5',side_effect=capture):result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped or not collected:return 1
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(collected[0],ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    r=collected[0];print(json.dumps({'result_file':str(args.output),'status':r['status'],'runtime_seconds':r['runtime_seconds'],'solver_seconds':r['physical_solver_result']['seconds'],'option_count':r['physical_solver_result']['option_count'],'selected_berths':r['selected_berths'],'selected_etbs':r['selected_etbs']},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':sys.exit(main())
