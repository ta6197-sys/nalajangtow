"""Run explicit synthetic evidence and approved real-V5 simulation examples."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from tests import test_v5_cost_search as cases
from src.daily_planning.integrated_v3.v5_cost_search import optimize_v5


def main():
    captured=[]
    def capture(*args,**kwargs):
        r=optimize_v5(*args,**kwargs);captured.append(r);return r
    suite=unittest.TestSuite([cases.V5CostSearchTest('test_two_valid_schedules_choose_lower_exact_engine_cost'),cases.V5RealDataUnresolvedTest('test_actual_v5_without_event_evidence_is_unresolved'),cases.V5RealDataUnresolvedTest('test_actual_v5_user_approved_clear_weather_simulation')])
    with patch.object(cases,'optimize_v5',side_effect=capture):result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped or len(captured)!=3:return 1
    out=ROOT/'results/v5_cost_search/examples.json';out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({'isolated_synthetic_explicit_events':captured[0],'actual_v5_without_evidence':captured[1],'actual_v5_approved_simulation_only':captured[2]},ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    for name,r in zip(['synthetic','actual_without_evidence','actual_simulation'],captured):
        print(name,json.dumps({'status':r['optimization_status'],'costs_cents':[x['total_demurrage_cents'] for x in r['evaluated_candidates']],'best_cents':r['best_total_demurrage_cents'],'evaluated':r['evaluated_schedule_count'],'solver_attempts':r['solver_attempt_count'],'runtime_seconds':r['search_runtime_seconds'],'stopping_reason':r['stopping_reason'],'optimality_scope':r['optimality_scope']},ensure_ascii=False))
    print('report',out);return 0
if __name__=='__main__':sys.exit(main())
