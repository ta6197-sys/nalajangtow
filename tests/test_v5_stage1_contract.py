"""Isolated regression fixtures for Stage-1; no solver/DB required."""
import copy
from datetime import datetime, timedelta, timezone
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.daily_planning.integrated_v3.v5_stage1_contract import (
    SimulationContractError, fingerprint, required_horizon_end,
    classify_coverage, classify_run, build_window_descriptor,
    inspect_execution_coverage, export_window_descriptor,
)

START = "2026-11-03T00:00:00+09:00"


def fixture():
    cfg = {"source_fingerprint":"source", "policy_fingerprint":"policy",
           "rolling_optimizer_mode":"INTEGRATED_COST_AWARE_V1",
           "objective_mode":"MIN_TOTAL_DEMURRAGE", "inventory_constraint_mode":"EXACT_EVENT_BALANCE_V1",
           "scope":["SHIP-1", "SHIP-2"], "start_at":START}
    manifest = {"config":cfg, "run_fingerprint":fingerprint(cfg)}
    prior = {"at":START,"completed_rows":[],"inventory":{"TANK-1":"50"}}
    row = {"call_id":"SHIP-1", "berth_id":"BERTH-01",
           "etb_at":"2026-11-03T04:00:00+09:00",
           "work_start_at":"2026-11-03T05:00:00+09:00",
           "work_end_at":"2026-11-03T08:00:00+09:00",
           "etu_at":"2026-11-03T09:00:00+09:00",
           "work_intervals":[["2026-11-03T05:00:00+09:00","2026-11-03T08:00:00+09:00"]]}
    candidate={"status":"VALIDATED", "selected_schedule":[row],
               "total_demurrage_cents":123,
               "tank_result":{"inventories":{"TANK-1":[{"at":START,"inventory_bbl":"50"}]},
                              "transfer_definitions":[{"call_id":"SHIP-1", "segments":[]}]},
               "vessel_demurrage_breakdown":{"SHIP-1":{"demurrage_cents":123}},
               "optimizer_result":{"candidate_domain_fingerprint":"domain"}}
    nxt = {"at":"2026-11-04T00:00:00+09:00","inventory":{"TANK-1":"80"},
           "cargo_progress":{"SHIP-1":{"remaining_bbl":"0","completed_bbl":"30"}}}
    cp={"window_id":1,"run_fingerprint":manifest["run_fingerprint"],
        "optimization_configuration":cfg, "input_fingerprint":"source", "policy_fingerprint":"policy",
        "as_of_at":START,"planning_horizon_end":"2026-11-06T00:00:00+09:00",
        "prior_state_fingerprint":fingerprint(prior),
        "next_state_fingerprint":fingerprint(nxt),"next_state":nxt,
        "selected_schedule":[row],"candidate":candidate,
        "carried_inventory":nxt["inventory"],"cargo_progress":nxt["cargo_progress"],
        "protected_commitments_in":[],
        "validation_results":{"physical":"VALID", "inventory":"VALID", "settlement":"SETTLED","carryover":"VALID"},
        "summary":{"window_id":1,"as_of_at":START,"window_demurrage_cents":123}}
    cp["checkpoint_fingerprint"]=fingerprint(cp)
    return manifest, cp, prior


class Stage1ContractTests(unittest.TestCase):
    def test_s20_means_3_windows_not_20(self):
        self.assertEqual(required_horizon_end(START,3),"2026-11-08T00:00:00+09:00")
        self.assertEqual(required_horizon_end(START,20),"2026-11-25T00:00:00+09:00")

    def test_window_completion_without_all_ships_is_not_failure(self):
        r=classify_run({"status":"REQUESTED_WINDOWS_VALIDATED","requested_windows":3,
                        "validated_window_count":3,"completed_scope_count":17,
                        "requested_scope_count":20,"all_scope_calls_completed":False})
        self.assertEqual(r["execution_status"], "COMPLETED_REQUESTED_WINDOWS")
        self.assertEqual(r["completion_status"], "CALLS_REMAIN")

    def test_unknown_failure_never_becomes_success(self):
        r=classify_run({"status":"STOPPED_UNRESOLVED","requested_windows":3,
                        "validated_window_count":1,"completed_scope_count":0,
                        "requested_scope_count":20,"all_scope_calls_completed":False,
                        "stopping_reason":{"code":"WINDOW_NOT_VALIDATED"}})
        self.assertEqual(r["execution_status"], "STOPPED_UNRESOLVED")

    def test_coverage_categories_remain_separate(self):
        a=classify_coverage({"status":"BLOCKED","blockers":[{"code":"FIXED_FLOW_HORIZON_SHORT"}]})
        b=classify_coverage({"status":"BLOCKED","blockers":[{"code":"DECLARED_BERTH_WINDOW_HORIZON_SHORT"}]})
        self.assertNotEqual(a["reason_category"],b["reason_category"])

    def test_export_preserves_exact_raw_data_and_marks_non_replay(self):
        manifest,cp,prior=fixture()
        out=build_window_descriptor(manifest,cp,prior_state=prior)
        self.assertEqual(out["selected_schedule"],cp["selected_schedule"])
        self.assertEqual(out["tank_result"],cp["candidate"]["tank_result"])
        self.assertEqual(out["unassigned_call_ids_at_window_start"],["SHIP-2"])
        self.assertEqual(out["window_projected_demurrage_cents"],123)
        self.assertEqual(out["independent_simulation_replay_status"],"NOT_RUN")
        self.assertEqual(out["export_status"],"DESCRIPTOR_ONLY_NOT_REPLAY_VALIDATED")
        self.assertIsNone(build_window_descriptor(manifest,cp)["unassigned_call_ids_at_window_start"])

    def test_tampering_is_blocked(self):
        m,cp,prior=fixture()
        cp["selected_schedule"][0]["etb_at"]="2026-11-04T01:00:00+09:00"
        with self.assertRaises(SimulationContractError):build_window_descriptor(m,cp,prior_state=prior)

    def test_missing_or_wrong_prior_state_is_not_silently_accepted(self):
        m,cp,prior=fixture()
        prior["inventory"]["TANK-1"]="999"
        with self.assertRaises(SimulationContractError):build_window_descriptor(m,cp,prior_state=prior)

    def test_later_window_requires_prior_state(self):
        m,cp,prior=fixture()
        cp['window_id']=2
        cp.pop('checkpoint_fingerprint')
        cp['checkpoint_fingerprint']=fingerprint(cp)
        with self.assertRaisesRegex(SimulationContractError, 'Prior rolling state required'):
            build_window_descriptor(m,cp)

    def test_duplicate_call_rejected_even_with_valid_hash(self):
        m,cp,prior=fixture()
        cp['selected_schedule'].append(copy.deepcopy(cp['selected_schedule'][0]))
        cp['candidate']['selected_schedule']=copy.deepcopy(cp['selected_schedule'])
        cp.pop('checkpoint_fingerprint')
        cp['checkpoint_fingerprint']=fingerprint(cp)
        with self.assertRaisesRegex(SimulationContractError, 'duplicate'):
            build_window_descriptor(m,cp,prior_state=prior)

    def test_false_all_scope_completion_is_rejected(self):
        with self.assertRaises(SimulationContractError):
            classify_run({"status":"REQUESTED_WINDOWS_VALIDATED","requested_windows":3,
                         "validated_window_count":3,"completed_scope_count":19,
                         "requested_scope_count":20,"all_scope_calls_completed":True})

    def test_invalid_coverage_does_not_claim_normal_completion(self):
        report={"status":"REQUESTED_WINDOWS_VALIDATED","requested_windows":3,
                "validated_window_count":3,"completed_scope_count":20,
                "requested_scope_count":20,"all_scope_calls_completed":True}
        r=classify_run(report,coverage={"status":"BLOCKED","blockers":[{"code":"FIXED_FLOW_HORIZON_SHORT"}]})
        self.assertEqual(r["execution_status"],"BLOCKED_INPUT_COVERAGE")

    def test_daylight_missing_is_blocked(self):
        snapshot={'fixed_process_flows':{}, 'tanks':[], 'berths':[]}
        config={'as_of_at':START, 'policy':{}, 'pilotage_context':{}}
        result=inspect_execution_coverage(snapshot,config,3)
        self.assertEqual(result['status'],'BLOCKED')
        self.assertIn('MISSING_OR_INVALID_DAYLIGHT_PILOTAGE', [b['code'] for b in result['blockers']])

    def test_coverage_block_never_calls_optimizer(self):
        from tests.test_v5_rolling import rolling_fixture
        from src.daily_planning.integrated_v3.v5_scale import execute_scale
        snapshot,policy,context=rolling_fixture()
        config={'data_dir':'unused','policy':policy,'pilotage_context':context,'as_of_at':START,
                'pricing_policy':'CLEAR_WEATHER_BASELINE_V1','clock_policy':'BLOCK_UNVERIFIED_CLOCK',
                'settlement_records':[]}
        coverage={'status':'BLOCKED','blockers':[{'code':'FIXED_FLOW_HORIZON_SHORT'}]}
        with tempfile.TemporaryDirectory() as root, \
             patch('src.daily_planning.integrated_v3.v5_stage1_contract.inspect_execution_coverage',return_value=coverage), \
             patch('src.daily_planning.integrated_v3.v5_rolling.run_v5_rolling') as runner:
            report=execute_scale(config,output_root=root,call_count=3,windows=1,snapshot=snapshot)
            runner.assert_not_called()
            self.assertEqual(report['execution_outcome_v1']['execution_status'],'BLOCKED_INPUT_COVERAGE')
            self.assertEqual(report['status'],'INPUT_COVERAGE_BLOCKED')

    def test_export_from_existing_run_is_create_only(self):
        run=Path('results/v5_pr79_primary_s5/runs/v5-fb17cf4ba6f24ba6')
        if not run.is_dir():
            self.skipTest('Validated local run unavailable')
        with tempfile.TemporaryDirectory() as root:
            path=export_window_descriptor(run,1,root)
            original=path.read_bytes()
            with self.assertRaises(FileExistsError):
                export_window_descriptor(run,1,root)
            self.assertEqual(path.read_bytes(),original)

if __name__ == "__main__":
    unittest.main()
