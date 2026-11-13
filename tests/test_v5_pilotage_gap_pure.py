"""Run without the full V5 repository: python -m unittest discover -s tests -p 'test_v5_pilotage_gap_pure.py'."""
import importlib.util
import pathlib
import unittest
from datetime import datetime, timedelta

ROOT=pathlib.Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('v5_pilotage_gap',ROOT/'src/daily_planning/integrated_v3/v5_pilotage_gap.py')
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)
AT='2026-11-05T00:00:00+09:00'
SUNSET='2026-11-07T17:22:00+09:00'

def item(cid='DEM-0030',eta='2026-11-07T18:55:00+09:00',fixed=False):
    return {'call_id':cid,'eta_at':eta,'fixed_etb_is_hard':fixed}

def policy(cid='DEM-0030', duration=5400,windows=None):
    if windows is None:
        windows=[{'start_at':'2026-11-07T06:50:00+09:00','end_at':SUNSET,'latest_start_at':'2026-11-07T15:52:00+09:00'},
                 {'start_at':'2026-11-08T06:51:00+09:00','end_at':'2026-11-08T17:22:00+09:00','latest_start_at':'2026-11-08T15:52:00+09:00'}]
    return {'berths':{'BERTH-01':{'berthing_seconds':duration,'pilot_boarding_to_berthing_seconds':0,
         'pilotage_windows_by_call':{cid:{'berthing':windows}}}}}

class Tests(unittest.TestCase):
    def test_s20_dem0030_no_slot_within_horizon(self):
        p=M.certify_no_pilotage_slot(item(),['BERTH-01'],policy(),AT)
        self.assertEqual(p['horizon_end_exclusive'],'2026-11-08T00:00:00+09:00')
        self.assertEqual(p['berth_evidence']['BERTH-01']['next_pilotage_only_etb_lower_bound'], '2026-11-08T08:21:00+09:00')
    def test_s20_dem0084_no_slot_within_horizon(self):
        p=M.certify_no_pilotage_slot(item('DEM-0084','2026-11-07T18:15:00+09:00'),['BERTH-01'],policy('DEM-0084'),AT)
        self.assertEqual(p['call_id'],'DEM-0084')
    def test_daylight_eta_is_not_deferred(self):
        self.assertIsNone(M.certify_no_pilotage_slot(item(eta='2026-11-07T09:00:00+09:00'),['BERTH-01'],policy(),AT))
    def test_earlier_next_day_is_not_deferred(self):
        self.assertIsNone(M.certify_no_pilotage_slot(item(eta='2026-11-07T18:55:00+09:00'),['BERTH-01'],policy(), '2026-11-06T00:00:00+09:00'))
    def test_fixed_etb_cannot_be_deferred(self):
        self.assertIsNone(M.certify_no_pilotage_slot(item(fixed=True),['BERTH-01'],policy(),AT))
    def test_future_eta_not_due(self):
        self.assertIsNone(M.certify_no_pilotage_slot(item(eta='2026-11-08T01:00:00+09:00'),['BERTH-01'],policy(),AT))
    def test_missing_windows_unknown_not_deferred(self):
        self.assertIsNone(M.certify_no_pilotage_slot(item(),['BERTH-01'],{'berths':{'BERTH-01':{'berthing_seconds':5400,'pilotage_windows_by_call':{}}}},AT))
    def test_all_eligible_berths_must_have_no_slot(self):
        p=policy();p['berths']['BERTH-02']={'berthing_seconds':3600,'pilotage_windows_by_call':{'DEM-0030':{'berthing':[{'start_at':'2026-11-07T19:00:00+09:00','end_at':'2026-11-07T23:00:00+09:00'}]}}}
        self.assertIsNone(M.certify_no_pilotage_slot(item(),['BERTH-01','BERTH-02'],p,AT))
    def test_any_unknown_berth_blocks_proof(self):
        self.assertIsNone(M.certify_no_pilotage_slot(item(),['BERTH-01','BERTH-11'],policy(),AT))
    def test_strict_horizon_end(self):
        window={'start_at':'2026-11-07T22:30:00+09:00','end_at':'2026-11-08T00:00:00+09:00'}
        self.assertIsNotNone(M.certify_no_pilotage_slot(item(),['BERTH-01'],policy(windows=[window]),AT))
    def test_maneuver_must_fit_within_window(self):
        window={'start_at':'2026-11-07T19:30:00+09:00','end_at':'2026-11-07T20:30:00+09:00'}
        self.assertIsNotNone(M.certify_no_pilotage_slot(item(),['BERTH-01'],policy(windows=[window]),AT))
    def test_invalid_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            M.certify_no_pilotage_slot(item(eta='2026-11-07T18:00:00'),['BERTH-01'],policy(),AT)
    def test_no_eligible_berths_does_not_prove_gap(self):
        self.assertIsNone(M.certify_no_pilotage_slot(item(),[],policy(),AT))
    def test_certificate_not_a_future_feasibility_claim(self):
        p=M.certify_no_pilotage_slot(item(),['BERTH-01'],policy(),AT)
        self.assertEqual(p['next_window_feasibility'],'NOT_PROVEN')
        self.assertEqual(p['physical_global_infeasibility'],'NOT_CLAIMED')

if __name__=='__main__':unittest.main()
