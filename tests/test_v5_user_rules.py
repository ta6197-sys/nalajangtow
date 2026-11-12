"""Deterministic pure tests of the documented V5 user operating policy."""
from datetime import datetime, timedelta, timezone
import unittest

from src.daily_planning.physical_scheduling.v5_user_rules import (
    BERTH_RULES, DOCK_PAIR, VERSION, HOSE_DISCONNECT_SECONDS,
    enabled, verify_v5_policy, pair_errors, no_night_etb,
)

KST=timezone(timedelta(hours=9))
START=datetime(2026,11,12,9,0,tzinfo=KST)


def at(hours):
    return (START+timedelta(hours=hours)).isoformat()


def vessel(cid,bid,etb,work_end,*,hose_start=None):
    return {
        'call_id':cid,'berth_id':bid,'cargo_group':'CRUDE',
        'berthing_start_at':at(etb-1),'etb_at':at(etb),
        'hose_connection_start_at':at(hose_start if hose_start is not None else etb),
        'hose_connection_end_at':at((hose_start if hose_start is not None else etb)+1),
        'work_intervals':[[at(work_end-1),at(work_end)]],
        'work_end_at':at(work_end),
        'hose_disconnect_end_at':at(work_end+2),
        'etu_at':at(work_end+2),
    }


def snapshot(grt_a='2000',grt_b='2000'):
    return {'calls':[
        {'call_id':'A','physical':{'grt':grt_a}},
        {'call_id':'B','physical':{'grt':grt_b}},
    ]}


def policy():
    x={'schema_version':VERSION,'data_origin':'SYNTHETIC_FIXTURE','operational_approval':False,
       'dock_4142_berths':['BERTH-07','BERTH-08'],'dock_4142_version':'USER_GRT3000_FIXED120_V1',
       'hose_disconnect_seconds':HOSE_DISCONNECT_SECONDS,'berths':{}}
    for bid,(_,d,s) in BERTH_RULES.items():
        x['berths'][bid]={'berthing_seconds':d,'direction_seconds':d,
                          'switching_seconds':s,'unberthing_seconds':0}
    return x


class UserRuleTests(unittest.TestCase):
    def test_complete_independent_berth_map(self):
        self.assertEqual(len(BERTH_RULES),11)
        self.assertEqual(BERTH_RULES['BERTH-03'],BERTH_RULES['BERTH-11'])
        self.assertEqual(BERTH_RULES['BERTH-04'],BERTH_RULES['BERTH-05'])
        self.assertEqual(DOCK_PAIR,{'BERTH-07','BERTH-08'})
        self.assertTrue(verify_v5_policy(policy(),BERTH_RULES))

    def test_malformed_rule_fails_closed(self):
        x=policy();x['berths']['BERTH-01']['unberthing_seconds']=3600
        with self.assertRaisesRegex(ValueError,'zero-ETU'):
            verify_v5_policy(x,BERTH_RULES)
        x=policy();x['hose_disconnect_seconds']=3600
        with self.assertRaisesRegex(ValueError,'120-minute'):
            verify_v5_policy(x,BERTH_RULES)

    def test_same_berth_switching_threshold(self):
        a=vessel('A','BERTH-01',0,5)  # ETU 16:00; switching 2.5h
        early=vessel('B','BERTH-01',9,12)
        late=vessel('B','BERTH-01',10,12)
        # A ETU offset 7; switching 2.5, so first valid ETB is 9.5
        self.assertIn('SAME_BERTH_SWITCHING',pair_errors(a,early,snapshot(),policy()))
        self.assertEqual(pair_errors(a,late,snapshot(),policy()),[])

    def test_same_berth_overlap_always_rejected(self):
        a=vessel('A','BERTH-07',0,5)
        b=vessel('B','BERTH-07',5,10)
        self.assertIn('SAME_BERTH_OCCUPANCY',pair_errors(a,b,snapshot(),policy()))

    def test_duplicate_reference_facility_is_independent(self):
        a=vessel('A','BERTH-03',0,5)
        b=vessel('B','BERTH-11',0,5)
        self.assertEqual(pair_errors(a,b,snapshot(),policy()),[])
        a['berth_id']='BERTH-04';b['berth_id']='BERTH-05'
        self.assertEqual(pair_errors(a,b,snapshot(),policy()),[])

    def test_dock_overlap_exact_2h_and_delayed_hose(self):
        a=vessel('A','BERTH-07',0,5)   # A work end 14:00; A ETU 16:00
        b=vessel('B','BERTH-08',5,10,hose_start=7)
        self.assertEqual(pair_errors(a,b,snapshot(),policy()),[])
        b['hose_connection_start_at']=at(5)
        b['hose_connection_end_at']=at(6)
        self.assertIn('DOCK_SHARED_HOSE_NOT_RELEASED',pair_errors(a,b,snapshot(),policy()))

    def test_dock_overlap_not_2h_rejected(self):
        a=vessel('A','BERTH-07',0,5)
        b=vessel('B','BERTH-08',6,10,hose_start=7)
        self.assertIn('DOCK_EXACT_120_MIN_OVERLAP_REQUIRED',pair_errors(a,b,snapshot(),policy()))

    def test_dock_large_ship_cannot_overlap(self):
        a=vessel('A','BERTH-07',0,5)
        b=vessel('B','BERTH-08',5,10,hose_start=7)
        self.assertIn('DOCK_NONSMALL_OVERLAP',pair_errors(a,b,snapshot(grt_a='3001'),policy()))

    def test_dock_3000_boundary_is_small(self):
        a=vessel('A','BERTH-07',0,5)
        b=vessel('B','BERTH-08',5,10,hose_start=7)
        self.assertEqual(pair_errors(a,b,snapshot('3000','3000'),policy()),[])

    def test_dock_sequential_switching_applies_to_following_call(self):
        a=vessel('A','BERTH-07',0,5)  # A ETU 16:00; switching 1h
        b=vessel('B','BERTH-08',7.5,12)
        self.assertIn('DOCK_SEQUENTIAL_SWITCHING',pair_errors(a,b,snapshot(),policy()))
        b=vessel('B','BERTH-08',8,12)
        self.assertEqual(pair_errors(a,b,snapshot(),policy()),[])

    def test_night_restriction_only_resolved_grt_range(self):
        inside=datetime(2026,11,12,3,30,tzinfo=KST)
        outside=datetime(2026,11,12,8,0,tzinfo=KST)
        self.assertTrue(no_night_etb({'physical':{'grt':'40000'}},inside))
        self.assertFalse(no_night_etb({'physical':{'grt':'40000'}},outside))
        self.assertFalse(no_night_etb({'physical':{'grt':'2000'}},inside))

if __name__=='__main__':unittest.main()
