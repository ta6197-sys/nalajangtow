import unittest
from src.daily_planning.risk.fractional_laytime import settle_intervals_exact_laytime
from src.daily_planning.risk.second_precision import settle_intervals_seconds

class FractionalLaytimeTest(unittest.TestCase):
    def setUp(self):
        self.contract={'nor_at':'2026-11-03T00:00:00+09:00','nor_valid':'Y','notice_hours':'0','allowed_minutes':'1','daily_demurrage_usd':'1440','day_mode':'SHINC','weather_policy':'HALF','post_demurrage_exceptions':'OWNER_ONLY','early_start_if_work':'N','despatch_policy':'NONE','holidays':[]}
        self.a='2026-11-03T00:00:00+09:00';self.b='2026-11-03T00:01:10+09:00';self.c='2026-11-03T00:03:00+09:00';self.events=[(self.a,self.b,'WEATHER'),(self.b,self.c,'WAIT')]
    def test_exact_policy_matches_existing_integer_allowance(self):
        a=settle_intervals_seconds(self.contract,self.a,self.c,self.events);b=settle_intervals_exact_laytime(self.contract,self.a,self.c,self.events,allowed_seconds=60)
        for key in ('demurrage_cents','laytime_used_seconds','demurrage_seconds','pre_excluded_seconds','post_excluded_seconds'):
            from fractions import Fraction
            self.assertEqual(Fraction(str(a[key])),Fraction(str(b[key])),key)
    def test_rational_allowance_preserves_boundary_and_cost(self):
        r=settle_intervals_exact_laytime(self.contract,self.a,self.c,self.events,allowed_seconds='3600/7')
        self.assertEqual(r['laytime_used_seconds'],'145');self.assertEqual(r['unused_laytime_seconds'],'2585/7');self.assertEqual(r['demurrage_cents'],0)
    def test_fractional_allowance_uses_existing_anchored_exhaustion(self):
        c=dict(self.contract,daily_demurrage_usd='86400');end='2026-11-03T00:01:01+09:00'
        r=settle_intervals_exact_laytime(c,self.a,end,[(self.a,end,'WAIT')],allowed_seconds='1/2')
        self.assertEqual(r['laytime_used_seconds'],'1/2');self.assertEqual(r['pre_excluded_seconds'],'119/2');self.assertEqual(r['demurrage_seconds'],'1');self.assertEqual(r['demurrage_cents'],100)
    def test_integer_allowances_match_existing_policy_over_event_combinations(self):
        from fractions import Fraction
        for allowed in (0,1,59,60,61,120):
            for weather in ('COUNT','EXCLUDE','HALF'):
                for post in ('NONE','OWNER_ONLY','OWNER_AND_TERMINAL'):
                    contract=dict(self.contract,allowed_minutes='0',weather_policy=weather,post_demurrage_exceptions=post)
                    events=[('2026-11-03T00:00:00+09:00','2026-11-03T00:00:45+09:00','WEATHER'),('2026-11-03T00:00:45+09:00','2026-11-03T00:01:30+09:00','OWNER_BREAKDOWN'),('2026-11-03T00:01:30+09:00','2026-11-03T00:03:00+09:00','WAIT')]
                    existing=settle_intervals_seconds(contract,self.a,self.c,events,allowed_seconds=allowed)
                    exact=settle_intervals_exact_laytime(contract,self.a,self.c,events,allowed_seconds=allowed)
                    for key in ('demurrage_cents','laytime_used_seconds','demurrage_seconds','pre_excluded_seconds','post_excluded_seconds','unused_laytime_seconds'):
                        self.assertEqual(Fraction(str(existing[key])),Fraction(str(exact[key])),(allowed,weather,post,key))
    def test_minute_aligned_matches_verified_contracts(self):
        from fractions import Fraction
        from contracts import settle_intervals
        end='2026-11-03T00:03:00+09:00';events=[(self.a,'2026-11-03T00:01:00+09:00','WEATHER'),('2026-11-03T00:01:00+09:00',end,'WAIT')]
        for allowed in (0,1,2,3):
            for weather in ('COUNT','EXCLUDE','HALF'):
                contract=dict(self.contract,allowed_minutes=str(allowed),weather_policy=weather)
                verified=settle_intervals(contract,self.a,end,events);exact=settle_intervals_exact_laytime(contract,self.a,end,events,allowed_seconds=allowed*60)
                self.assertEqual(verified['demurrage_cents'],exact['demurrage_cents'])
                self.assertEqual(Fraction(str(verified['demurrage_minutes']))*60,Fraction(exact['demurrage_seconds']))
