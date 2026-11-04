import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import patch

from rolling_horizon import RULE, workload_remaining
from weather_optimizer import WeatherSeries
from src.daily_planning.context import signature
from src.daily_planning.daylight import daylight_window, simulate_daylight_call, simulate_new_call
from src.daily_planning.evaluation import evaluate_baseline
from src.daily_planning.execution import execute_committed_window


def weather(blocked=()):
    start=datetime(2026,11,3)
    rows=[]
    for i in range(14*48):
        at=start+timedelta(minutes=30*i)
        rows.append({'일시':at.isoformat(),'풍속(m/s)':9 if at in blocked else 0,
                     '유의파고(m)':0,'최대파고(m)':0,'평균파고(m)':0})
    return WeatherSeries(rows)


def call(minutes=120):
    return dict(call_id='source-call',available_at='2026-11-03T00:00:00',processing_minutes=minutes,
        cargo_quantity_kg=minutes*1000,contract=dict(nor_at='2026-11-03T00:00:00',notice_hours=0,
            early_start_if_work='N',allowed_minutes=0,daily_demurrage_usd=1440,nor_valid='Y',
            day_mode='SHINC',weather_policy='EXCLUDE',post_demurrage_exceptions='NONE',
            despatch_policy='NONE',holidays=[]))


def snapshot(c):
    con=dict(c['contract']);con.pop('nor_at')
    raw=dict(experiment_key='test-daywork',as_of_at='2026-11-03T00:00:00+09:00',
        berthing_policy='DAYLIGHT_WORK_V2',contracts=[dict(contract_id='ct',allowed_minutes=0,
        demurrage_daily_cents=144000,contract_terms=con)],calls=[dict(call_id=c['call_id'],contract_id='ct',
        nor_at=c['available_at'],available_at=c['available_at'],work_minutes=c['processing_minutes'],
        cargo_quantity_kg=c['cargo_quantity_kg'])],berths=[dict(berth_id='B1',is_active=True)],commits=[],
        call_states=[dict(call_id=c['call_id'],status='WAITING',remaining_work_kg=c['cargo_quantity_kg'],
            accrued_demurrage_cents=0,contract_clock_state={'remaining_work_minutes':c['processing_minutes']})])
    raw['snapshot_hash']=signature(raw)
    return raw


class DaylightWorkTest(unittest.TestCase):
    def assert_safe_work(self,row,minutes):
        total=0
        for a,b in row['work_intervals']:
            a,b=datetime.fromisoformat(a),datetime.fromisoformat(b)
            rise,setting=daylight_window(a)
            self.assertLessEqual(rise,a);self.assertLess(a,b);self.assertLessEqual(b,setting)
            total+=int((b-a).total_seconds()/60)
        self.assertEqual(total,minutes)

    def test_sunset_stops_and_next_sunrise_resumes_exact_minutes(self):
        c=call();w=weather();row=simulate_daylight_call(c,datetime(2026,11,3,17),w,RULE)
        self.assertEqual(row['completion_at'],'2026-11-04T08:21:00')
        self.assertEqual(row['night_pause_intervals'],[['2026-11-03T17:26:00','2026-11-04T06:47:00']])
        self.assertEqual(workload_remaining(row,c,datetime(2026,11,3,23)),94)
        self.assertEqual(workload_remaining(row,c,datetime(2026,11,4,6)),94)
        self.assert_safe_work(row,120)

    def test_boundaries_and_weather_resume(self):
        w=weather((datetime(2026,11,4,7),));c=call(44)
        row=simulate_daylight_call(c,datetime(2026,11,4,6,47),w,RULE)
        self.assertEqual(row['completion_at'],'2026-11-04T08:01:00')
        self.assertEqual(row['weather_pause_minutes'],30)
        self.assert_safe_work(row,44)
        row=simulate_daylight_call(call(1),datetime(2026,11,3,17,26),w,RULE)
        self.assertEqual(row['work_start_at'],'2026-11-04T06:47:00')
        row=simulate_daylight_call(call(26),datetime(2026,11,3,17),w,RULE)
        self.assertEqual(row['completion_at'],'2026-11-03T17:26:00')
        self.assertEqual(row['night_pause_minutes'],0)

    def test_night_is_not_falsely_excluded_as_weather_from_contract(self):
        row=simulate_daylight_call(call(),datetime(2026,11,3,17),weather(),RULE)
        self.assertEqual(row['weather_pause_minutes'],0)
        self.assertEqual(row['demurrage_minutes'],1941)
        self.assertEqual(row['night_pause_minutes'],801)

    def test_multiday_all_segments_and_work_conservation(self):
        row=simulate_daylight_call(call(3000),datetime(2026,11,3,4),weather(),RULE)
        self.assertGreater(len(row['night_pause_intervals']),3)
        self.assert_safe_work(row,3000)

    def test_v1_history_is_unchanged(self):
        row=simulate_new_call(call(),datetime(2026,11,3,17),weather(),RULE)
        self.assertEqual(row['completion_at'],'2026-11-03T19:00:00')

    def test_evaluation_and_24h_execution_have_the_same_night_progress(self):
        c=call();s=snapshot(c);w=weather()
        row=simulate_daylight_call(c,datetime(2026,11,3,17),w,RULE)
        plan=[dict(call_id=c['call_id'],berth_id='B1',start_at=row['work_start_at'],completion_at=row['completion_at'])]
        result=evaluate_baseline(s,plan,w)['evaluation']
        self.assertEqual(result['vessel_results'][0]['remaining_work_minutes_at_24h'],94)
        s['commits']=[dict(call_id=c['call_id'],berth_id='B1',start_at=row['work_start_at'],completion_at=row['completion_at'],locked_from_at=row['work_start_at'],locked_until_at=row['completion_at'])]
        s['snapshot_hash']=signature({k:v for k,v in s.items() if k!='snapshot_hash'})
        actual=execute_committed_window(s,datetime(2026,11,4),w)['vessel_results'][0]
        self.assertEqual(actual['remaining_work_minutes_at_24h'],94)
        self.assertEqual(actual['demurrage_increment_24h_cents'],result['vessel_results'][0]['demurrage_increment_24h_cents'])
        self.assertEqual(actual['completion_at'],result['vessel_results'][0]['completion_at'])


if __name__=='__main__':unittest.main()
