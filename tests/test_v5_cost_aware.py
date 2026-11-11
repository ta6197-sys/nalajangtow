import copy
import itertools
import unittest
from unittest.mock import patch

from tests.test_v5_cost_search import fixture
from tests.test_v5_physical_integration import START
from src.daily_planning.integrated_v3.v5_physical import schedule_v5
from src.daily_planning.integrated_v3.v5_settlement import price_v5_schedule
from src.daily_planning.physical_scheduling.validator import validate_result


def make_case(n=2):
    s,p=fixture();first=s['calls'][0];original=first['call_id'];b=s['berths'][0];bid=b['berth_id']
    b2=copy.deepcopy(b);b2['berth_id']='B2';s['berths'].append(b2)
    s['tanks'][0]['connected_berth_ids'].append('B2');s['tanks'][0]['max_receipt_rate_bbl_per_hour']='300';p['berths']['B2']=copy.deepcopy(p['berths'][bid])
    for i in range(1,n):
        c=copy.deepcopy(first);c['call_id']='SYN-'+str(i+1);c['vessel_id']='V'+str(i+1);c['daily_demurrage_usd']=str(1440*(i+1));s['calls'].append(c)
        record=copy.deepcopy(s['workbook_tables']['contracts']['rows'][0]);record.update(case_id=c['call_id'],vessel_id=c['vessel_id'],demurrage_usd_day=c['daily_demurrage_usd']);s['workbook_tables']['contracts']['rows'].append(record)
    times=['2026-11-03T03:00:00+09:00','2026-11-03T06:00:00+09:00']
    return s,p,{c['call_id']:list(times) for c in s['calls']}


def run_case(s,p,times,**kwargs):
    with patch('src.daily_planning.integrated_v3.v5_physical._verified_snapshot',side_effect=lambda *a:copy.deepcopy(s)),patch('src.daily_planning.integrated_input.v5.validate_v5',return_value={'data_preflight_pass':True,'errors':[]}),patch('src.daily_planning.integrated_v3.v5_physical.prepare_pilotage_policy',side_effect=lambda s,c,e,p,*a:(p,None)):
        return schedule_v5(s,data_dir='SYNTHETIC_TEST',call_ids=list(times),as_of_at=START,policy=p,candidate_etbs=times,objective_mode='MIN_TOTAL_DEMURRAGE',pricing_policy='CLEAR_WEATHER_BASELINE_V1',**kwargs)


def exhaustive(s,p,result):
    options=[item['option'] for item in result['option_cost_breakdown']]
    groups=[[x for x in options if x['call_id']==c['call_id']] for c in s['calls']]
    costs=[]
    for rows in itertools.product(*groups):
        if validate_result(s,list(rows),p)['status']!='VALID':continue
        priced=price_v5_schedule(s,list(rows),as_of_at=START,settlement_records=[],pricing_policy='CLEAR_WEATHER_BASELINE_V1')
        if priced['status']=='SETTLED':costs.append(priced['total_demurrage_cents'])
    return sorted(costs)


class CostAwareTests(unittest.TestCase):
    def test_two_and_three_calls_match_independent_oracle(self):
        for n in (2,3):
            with self.subTest(n=n):
                s,p,times=make_case(n);r=run_case(s,p,times);self.assertEqual(r['status'],'PHYSICAL_AND_INVENTORY_VALID',r)
                costs=exhaustive(s,p,r);self.assertGreater(len(set(costs)),1)
                self.assertEqual(r['solver_status'],'OPTIMAL');self.assertEqual(r['total_projected_demurrage_cents'],costs[0]);self.assertTrue(r['simulation_only']);self.assertEqual(r['physical_validation']['status'],'VALID')
    def test_missing_settlement_never_zero(self):
        s,p,times=make_case();s['source_input']['data_origin']='NOT_SIMULATION';r=run_case(s,p,times)
        self.assertEqual(r['status'],'SETTLEMENT_UNRESOLVED');self.assertNotIn('total_projected_demurrage_cents',r)
    def test_same_etb_different_phase_cost(self):
        s,p,times=make_case();r=run_case(s,p,times)
        by_key={}
        for item in r['option_cost_breakdown']:
            row=item['option'];key=(row['call_id'],row['berth_id'],row['etb_at'])
            by_key.setdefault(key,set()).add(item['pricing']['demurrage_cents'])
        self.assertTrue(any(len(costs)>1 for costs in by_key.values()))
    def test_feasible_incumbent_never_claims_optimality(self):
        from ortools.sat.python import cp_model
        original=cp_model.CpSolver.Solve
        def feasible_only(solver,model,*args,**kwargs):
            status=original(solver,model,*args,**kwargs)
            return cp_model.FEASIBLE if status==cp_model.OPTIMAL else status
        s,p,times=make_case()
        with patch.object(cp_model.CpSolver,'Solve',feasible_only):r=run_case(s,p,times)
        self.assertEqual(r['solver_status'],'FEASIBLE');self.assertEqual(r['optimality_scope'],'PHYSICAL_CANDIDATE_COST_INCUMBENT_ONLY');self.assertIsNotNone(r['total_projected_demurrage_cents'])
    def test_hard_fix_and_commitment(self):
        s,p,times=make_case();c=s['calls'][0];c.update(fixed_etb_is_hard=True,fix_etb_at=times[c['call_id']][0]);r=run_case(s,p,times)
        self.assertEqual(r['selected_etbs'][c['call_id']],c['fix_etb_at'])
        locked=[next(row for row in r['selected_schedule'] if row['call_id']==c['call_id'])];r2=run_case(s,p,times,physical_commitments=locked)
        self.assertEqual(next(row for row in r2['selected_schedule'] if row['call_id']==c['call_id']),locked[0]);self.assertEqual(r2['total_projected_demurrage_cents'],r['total_projected_demurrage_cents'])
    def test_inventory_failure_preserves_costed_physical_candidate(self):
        s,p,times=make_case();s['tanks'][0]['capacity_bbl']='550';r=run_case(s,p,times)
        self.assertEqual(r['status'],'INVENTORY_VALIDATION_FAILED');self.assertEqual(r['physical_validation']['status'],'VALID');self.assertEqual(r['combined_validation'],'NOT_VALIDATED');self.assertIsNotNone(r['total_projected_demurrage_cents'])
