"""BALANCED JSON checks; real-source cases require the explicit data directory."""
import copy
import os
import unittest
from fractions import Fraction
from ortools.sat.python import cp_model

from src.daily_planning.integrated_input.loader import load_inputs
from src.daily_planning.integrated_input.normalizer import normalize
from src.daily_planning.integrated_input.v5 import validate_v5
from src.daily_planning.integrated_v3.v5_scale import bounded_parameters, ScaleInputError
from src.daily_planning.tank.v5_inventory import validate_v5_inventory
from src.daily_planning.integrated_v3.v5_physical import _verified_snapshot
from src.daily_planning.integrated_v3.v5_inventory_constraints import add_inventory_constraints
from src.daily_planning.integrated_v3.v5_stage1_evidence import required_window_end, audit_gate
from scripts.v5_stage1.run_integrated_gates import scenario_order
from tests.test_v5_stage1_gates import fixture_run
from tests.test_v5_tank_inventory import fixture, add, START, at


class BalancedBoundsTests(unittest.TestCase):
    def test_95_percent_is_hard_in_cp_sat(self):
        model=cp_model.CpModel();x=model.NewBoolVar('x')
        tank={'tank_id':'T','min_inventory_bbl':'100','capacity_bbl':'1000','effective_max_inventory_bbl':'950','max_receipt_rate_bbl_per_hour':'100','max_dispatch_rate_bbl_per_hour':'100'}
        prepared={'tanks':{'T':{'events':[0,1],'external_cumulative_bbl':[Fraction(0),Fraction(0)]}},
                  'options':[{'tank_id':'T','signed_cumulative_bbl':[Fraction(0),Fraction(40)],'receipt_rates_bbl_per_hour':[Fraction(40)],'dispatch_rates_bbl_per_hour':[Fraction(0)]}]}
        add_inventory_constraints(model,[x],prepared,[tank],initial_inventory={'T':'925'},option_count=1)
        model.Maximize(x); solver=cp_model.CpSolver()
        self.assertEqual(solver.Solve(model),cp_model.OPTIMAL)
        self.assertEqual(solver.Value(x),0)

    def test_full154_requires_62_day_horizon(self):
        self.assertEqual(required_window_end(START,60).isoformat(),'2027-01-03T15:00:00+00:00')

    def test_full154_requires_completion_and_is_not_a_legacy_gate(self):
        report,manifest,checkpoints,evidence,calls=fixture_run(completed=False)
        result=audit_gate(report,manifest,checkpoints,evidence,gate='FULL154',scope=report['scope_call_ids'],
                          expected_windows=3,source_fingerprint='source',replay_pass=True,source_calls=calls)
        self.assertEqual(result['status'],'BLOCKED_SCOPE_NOT_FULLY_COMPLETED')
        self.assertNotIn('FULL154',scenario_order({'scenario':'V5_3TANK'}))
        self.assertNotIn('FULL140',scenario_order({'scenario':'V5_BALANCED_154'}))

    def test_exact_95_percent_is_enforced_by_independent_replay(self):
        snapshot=fixture(); snapshot['scenario_id']='V5_BALANCED_154'
        snapshot['tanks'][0]['effective_max_inventory_bbl']='950'
        snapshot['tanks'][0]['initial_inventory_bbl']='925'
        transfer=add(snapshot,'A',rate=50)
        result=validate_v5_inventory(snapshot,[transfer],start_at=START,end_at=at(4))
        self.assertEqual(result['status'],'INVALID')
        self.assertIn('INVENTORY_OUT_OF_BOUNDS',[x['code'] for x in result['issues']])
        self.assertTrue(any(point['at']==at(1.5) for point in result['inventories']['T']))
        self.assertEqual(snapshot['tanks'][0]['capacity_bbl'],'1000')

    def test_scenario_specific_count_bound(self):
        args=dict(count=154,windows=60,time_limit_seconds=120,candidates_per_pair=6,total_candidates=240,improvement_rounds=2)
        bounded_parameters(**args,scenario='V5_BALANCED_154')
        with self.assertRaises(ScaleInputError):bounded_parameters(**args)


@unittest.skipUnless(os.environ.get('DOCKTIMIZE_BALANCED_DATA'), 'Explicit BALANCED JSON source unavailable')
class BalancedSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot=normalize(load_inputs(os.environ['DOCKTIMIZE_BALANCED_DATA'],scenario='V5_BALANCED_154'))

    def test_actual_two_json_sources_and_exact_counts(self):
        result=validate_v5(self.snapshot)
        self.assertTrue(result['data_preflight_pass'],result['errors'])
        self.assertEqual((result['vessel_count'],result['hard_fix_count'],result['flow_record_count']),(154,15,696))
        self.assertEqual(len(self.snapshot['artifacts']),2)
        self.assertEqual(self.snapshot['scenario_id'],'V5_BALANCED_154')
        self.assertEqual(len(self.snapshot['workbook_tables']['restrictions']['rows']),17)
        self.assertEqual(len(self.snapshot['workbook_tables']['groups']['rows']),3)
        self.assertTrue(all(Fraction(t['effective_max_inventory_bbl'])==9500000 for t in self.snapshot['tanks']))

    def test_cross_scenario_replay_does_not_accept_balanced_files(self):
        wrong=copy.deepcopy(self.snapshot);wrong['scenario_id']='V5_3TANK'
        with self.assertRaises(ValueError):
            _verified_snapshot(wrong,os.environ['DOCKTIMIZE_BALANCED_DATA'])

    def test_reject_wrong_flow_reference_and_provenance(self):
        snapshot=copy.deepcopy(self.snapshot)
        snapshot['source_input']['process_flow_file']='wrong.json'
        self.assertEqual(validate_v5(snapshot)['input_status'],'INPUT_INVALID')
        snapshot=copy.deepcopy(self.snapshot)
        snapshot['source_input']['balanced_enrichment_provenance']['external_flow_json_sha256']='0'*64
        self.assertEqual(validate_v5(snapshot)['input_status'],'INPUT_INVALID')
        snapshot=copy.deepcopy(self.snapshot)
        snapshot['source_input']['source_workbook_sha256']='f'*64
        self.assertEqual(validate_v5(snapshot)['input_status'],'INPUT_INVALID')

    def test_reject_invalid_contract_restriction_and_count(self):
        for mutate in ('contract','restriction','count'):
            snapshot=copy.deepcopy(self.snapshot)
            if mutate=='contract':snapshot['calls'][0]['allowance_value']='1'
            elif mutate=='restriction':snapshot['source_input']['berth_restrictions'].pop()
            else:snapshot['calls'].pop()
            self.assertEqual(validate_v5(snapshot)['input_status'],'INPUT_INVALID',mutate)

    def test_profile_identity_fix_and_flow_gap_are_rejected(self):
        for kind in ('profile','fix','gap'):
            snapshot=copy.deepcopy(self.snapshot)
            if kind=='profile':snapshot['source_input']['vessel_profiles'][0]['vessel_id']='WRONG'
            elif kind=='fix':
                fixed=next(c for c in snapshot['calls'] if c['fixed_etb_is_hard'])
                fixed['fix_etb_at']='2026-01-01T00:00:00+09:00'
            else:snapshot['fixed_process_flows']['segments'][0]['end_at']='2026-11-03T05:00:00+09:00'
            self.assertEqual(validate_v5(snapshot)['input_status'],'INPUT_INVALID',kind)


if __name__=='__main__':unittest.main()
