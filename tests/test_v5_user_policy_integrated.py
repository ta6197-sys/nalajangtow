"""Integration assertions for the patched existing V5 physical modules.

These tests must execute in the full Docktimize repository AFTER the installer
has applied the versioned user policy. No mocks of CP-SAT are used here.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.daily_planning.physical_scheduling.phases import replay
from src.daily_planning.physical_scheduling.conflict_index import iter_potential_pairs
from src.daily_planning.physical_scheduling.validator import pair_conflicts
from src.daily_planning.physical_scheduling.v5_user_rules import BERTH_RULES, VERSION, DOCK_VERSION

KST=timezone(timedelta(hours=9))
DAY='2026-11-12'


def time(hour):
    return (datetime(2026,11,12,tzinfo=KST)+timedelta(hours=hour)).isoformat()


def context():
    berth={'berth_id':'BERTH-01','active':'Y', 'allowed_cargo_groups':'CRUDE',
           'allowed_operations':'IMPORT','max_dwt_mt':'50000','min_loa_m':'5',
           'max_loa_m':'200','max_draft_m':'20','min_pbl_m':'0',
           'draft_check_operation':'IMPORT','max_transfer_rate_mt_h':'100',
           'hose_disconnect_min':'15'}  # Old source value: V5 explicitly supersedes it.
    call={'call_id':'A','vessel_id':'VA','cargo_group':'CRUDE','direction':'IMPORT',
          'cargo_quantity_mt':'100','transfer_rate_mt_h':'100','eta_at':time(9),
          'fixed_etb_is_hard':False,'physical':{'dwt_mt':'10000','loa_m':'100',
                                                'draft_m':'5','pbl_m':'50','grt':'2000'}}
    policy={'schema_version':VERSION,'cargo_duration_policy':'VESSEL_BERTH_RATE_V1',
            'hose_disconnect_seconds':7200, 'dock_4142_version':DOCK_VERSION,
            'berths':{'BERTH-01':{'berthing_seconds':5400,'direction_seconds':5400,
                                  'switching_seconds':9000,'unberthing_seconds':0,
                                  'hose_connection_seconds':7200,
                                  'work_windows':[{'start_at':time(0),'end_at':time(48)}],
                                  'pilotage_windows':[{'start_at':time(0),'end_at':time(48)}]}}}
    return call,berth,policy


class IntegratedUserPhaseTests(unittest.TestCase):
    def test_etu_is_hose_end_not_extra_departure_60min(self):
        call,berth,policy=context()
        row=replay(call,berth,time(11),policy)
        self.assertEqual(row['work_end_at'],time(14))
        self.assertEqual(row['hose_disconnect_end_at'],time(16))
        self.assertEqual(row['etu_at'],time(16))
        self.assertEqual(row['switching_end_at'],time(16))
        self.assertEqual(row['phase_policy_version'],VERSION)
        self.assertEqual(row['direction_seconds'],5400)
        self.assertEqual(row['switching_rule_seconds'],9000)
        with self.assertRaisesRegex(ValueError,'ETU is fixed'):
            replay(call,berth,time(11),policy,unberthing_start_at=time(17))

    def test_same_berth_etu_plus_switch_not_cut_by_fast_pair_filter(self):
        call,berth,policy=context()
        a=replay(call,berth,time(11),policy)
        b=replay({**call,'call_id':'B'},berth,time(18),policy)
        snapshot={'workbook_tables':{'groups':{'rows':[]}},'calls':[call,{**call,'call_id':'B'}]}
        self.assertIn('SAME_BERTH_SWITCHING',pair_conflicts(a,b,snapshot,policy))
        self.assertEqual(list(iter_potential_pairs(snapshot,policy,[a,b])),[(0,1)])

    def test_24h_etu_does_not_require_departure_window(self):
        call,berth,policy=context()
        policy['berths']['BERTH-01']['pilotage_windows_by_call']={'A':{
          'berthing':[{'start_at':time(0),'end_at':time(48)}]}}
        row=replay(call,berth,time(11),policy)
        self.assertEqual(row['etu_at'],time(16))
        self.assertIsNone(policy['berths']['BERTH-01']['pilotage_windows_by_call']['A'].get('unberthing'))


if __name__=='__main__':unittest.main()
