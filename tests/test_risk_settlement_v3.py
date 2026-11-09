"""Independent second-boundary amounts and all-source Golden validation."""
from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from src.daily_planning.risk.golden_validation import validate_golden_workbook
from src.daily_planning.risk.replay import (_physical_replay, evaluate_scenario,
    NO_SHOCK_BASELINE, SECOND_PRECISION_SIMULATION_V1)
from src.daily_planning.risk.second_precision import (
    contract_clock_start_seconds, settle_intervals_seconds,
    ASSUMPTION_VERSION)
from src.daily_planning.risk.settlement import project_minute_boundary
from contracts import settle_intervals
from src.daily_planning.risk.weather_overlay import scenario_weather
from src.daily_planning.context import local_time
from .test_risk_arrival_replay import fixture, saved_candidate
from .test_risk_replay_v2 import ongoing_fixture
from .test_risk_settlement_precision import contract


ROOT = Path(__file__).resolve().parents[1]


class RiskSettlementV3Test(unittest.TestCase):
    def test_all_144_original_golden_fields_and_clock_divergences(self):
        report = validate_golden_workbook(ROOT/'input/source_contracts.xlsx')
        self.assertEqual(report['contract_count'], 144)
        self.assertEqual(report['sof_event_count'], 1639)
        self.assertEqual(report['golden_label_count'], 144)
        self.assertEqual(report['exact_settlement_parity_count'], 144,
                         report['settlement_mismatches'])
        self.assertEqual(report['settlement_mismatches'], [])
        self.assertEqual(report['clock_start_divergence_count'], 40)
        self.assertEqual(report['derived_clock_golden_mismatch_count'], 133)
        self.assertTrue(report['settlement_parity_complete'])

    def test_no_shock_abc_exact_baseline_with_second_policy(self):
        for label in 'ABC':
            snap,candidate = saved_candidate(label)
            result = evaluate_scenario(snap,candidate,
                {'scenario_id': NO_SHOCK_BASELINE},
                settlement_policy=SECOND_PRECISION_SIMULATION_V1)
            self.assertEqual(result['cost_cents'],candidate['cost_cents'])
            self.assertEqual(result['vessel_results'],candidate['vessel_results'])

    def test_zero_allowance_one_29_30_59_seconds_exact_money(self):
        start = datetime(2026,11,2,10)
        terms = contract(daily_demurrage_usd='1440')
        for seconds,cents in ((1,2),(29,48),(30,50),(59,98)):
            finish = start+timedelta(seconds=seconds)
            result = settle_intervals_seconds(terms,start,finish,
                [(start,finish,'WAIT')])
            self.assertEqual(result['demurrage_seconds'],str(seconds))
            self.assertEqual(result['demurrage_cents'],cents)
            self.assertEqual(result['first_demurrage_at'],start.isoformat())

    def test_nor_notice_and_early_work_start_at_seconds(self):
        nor = datetime(2026,11,2,10,0,29)
        work = datetime(2026,11,2,10,0,30)
        terms = contract(nor_at=nor.isoformat(),notice_hours=1,
                         early_start_if_work='N')
        self.assertEqual(contract_clock_start_seconds(terms,work),
                         datetime(2026,11,2,11,0,29))
        terms['early_start_if_work']='Y'
        self.assertEqual(contract_clock_start_seconds(terms,work),work)

    def test_exhaustion_uses_effective_start_anchor_not_event_start(self):
        start = datetime(2026,11,2,10,0,17)
        a=start+timedelta(seconds=29)
        b=a+timedelta(seconds=60)
        c=b+timedelta(seconds=14)
        finish=start+timedelta(minutes=3)
        result=settle_intervals_seconds(contract(allowed_minutes=1,
            weather_policy='HALF',daily_demurrage_usd='1440'),start,finish,
            [(start,a,'WAIT'),(a,b,'WEATHER'),(b,c,'WAIT'),(c,finish,'WEATHER')])
        self.assertEqual(result['laytime_used_seconds'],'60.0')
        self.assertEqual(result['pre_excluded_seconds'],'60.0')
        self.assertEqual(result['demurrage_seconds'],'60.0')
        self.assertEqual(result['first_demurrage_at'],'2026-11-02T10:02:17')
        self.assertEqual(result['demurrage_cents'],100)
        self.assertEqual(result['settlement_assumption_version'],ASSUMPTION_VERSION)

    def test_weekend_holiday_and_saturday_noon(self):
        start=datetime(2026,11,7,11,59,59)
        finish=start+timedelta(seconds=2)
        events=[(start,finish,'WAIT')]
        sat=settle_intervals_seconds(contract(allowed_minutes=1,
            day_mode='SHEX_SAT12'),start,finish,events)
        self.assertEqual(sat['laytime_used_seconds'],'1')
        self.assertEqual(sat['pre_excluded_seconds'],'1')
        weekend=settle_intervals_seconds(contract(allowed_minutes=1,
            day_mode='SHEX_WEEKEND'),start,finish,events)
        self.assertEqual(weekend['laytime_used_seconds'],'0')
        self.assertEqual(weekend['pre_excluded_seconds'],'2')
        shinc=settle_intervals_seconds(contract(allowed_minutes=1,
            day_mode='SHINC',holidays=['2026-11-07']),start,finish,events)
        self.assertEqual(shinc['laytime_used_seconds'],'2')
        sunday=datetime(2026,11,8,10,0,17)
        sun=settle_intervals_seconds(contract(allowed_minutes=1,
            day_mode='SHEX_SUN'),sunday,sunday+timedelta(seconds=2),
            [(sunday,sunday+timedelta(seconds=2),'WAIT')])
        self.assertEqual(sun['pre_excluded_seconds'],'2')

    def test_weather_credit_exclude_half_count(self):
        start=datetime(2026,11,2,10,0,17);finish=start+timedelta(minutes=1)
        expected={'EXCLUDE':('0','60'),'HALF':('30.0','30.0'),
                  'COUNT':('60','0')}
        for policy,(used,excluded) in expected.items():
            result=settle_intervals_seconds(contract(allowed_minutes=1,
                weather_policy=policy),start,finish,[(start,finish,'WEATHER')])
            self.assertEqual((result['laytime_used_seconds'],
                              result['pre_excluded_seconds']),(used,excluded))
        tiny=start+timedelta(seconds=1)
        half=settle_intervals_seconds(contract(allowed_minutes=1,
            weather_policy='HALF'),start,tiny,[(start,tiny,'WEATHER')])
        self.assertEqual(half['laytime_used_seconds'],'0.5')
        self.assertEqual(half['pre_excluded_seconds'],'0.5')

    def test_pre_post_exceptions_and_despatch(self):
        start=datetime(2026,11,2,10,0,17)
        a=start+timedelta(seconds=20)
        b=a+timedelta(seconds=60)
        c=b+timedelta(seconds=60)
        finish=start+timedelta(minutes=3)
        events=[(start,a,'OWNER_BREAKDOWN'),(a,b,'WAIT'),
                (b,c,'TERMINAL_STOP'),(c,finish,'WAIT')]
        terms=contract(allowed_minutes=1,daily_demurrage_usd='1440',
                       post_demurrage_exceptions='OWNER_AND_TERMINAL')
        result=settle_intervals_seconds(terms,start,finish,events)
        self.assertEqual(result['pre_excluded_seconds'],'60')
        self.assertEqual(result['post_excluded_seconds'],'20')
        self.assertEqual(result['demurrage_seconds'],'40')
        self.assertEqual(result['demurrage_cents'],67)
        terms['post_demurrage_exceptions']='NONE'
        self.assertEqual(settle_intervals_seconds(terms,start,finish,events)
                         ['demurrage_cents'],100)
        short=start+timedelta(seconds=29)
        despatch=settle_intervals_seconds(contract(allowed_minutes=1,
            despatch_policy='HALF_WORKING_TIME',daily_demurrage_usd='1440'),
            start,short,[(start,short,'WAIT')])
        self.assertEqual(despatch['unused_laytime_seconds'],'31')
        self.assertEqual(despatch['despatch_cents'],26)

    def test_second_physics_weather_combined_shocks_and_cargo(self):
        snap,candidate=fixture()
        scenario=dict(scenario_id='combined-seconds',
            weather_blocks=[{'onset_offset_minutes':25*60+15,
                'onset_offset_seconds':17,'duration_minutes':30},
                {'onset_offset_minutes':25*60+25,
                 'onset_offset_seconds':17,'duration_minutes':20}],
            call_shocks={'RISK-001':{'available_at_shift_seconds':17,
                                     'work_duration_multiplier':'1.25'}})
        result=evaluate_scenario(snap,candidate,scenario,
            settlement_policy=SECOND_PRECISION_SIMULATION_V1)
        self.assertEqual((result['physical_status'],result['settlement_status']),
                         ('FEASIBLE','SUCCEEDED'))
        row=next(r for r in result['actual_schedules'] if r['call_id']=='RISK-001')
        self.assertEqual(row['work_start_at'],'2026-11-02T01:00:17+09:00')
        self.assertTrue(any(datetime.fromisoformat(a).second==17
            for a,_ in row['weather_pause_intervals']))
        self.assertEqual(row['completed_work_kg_at_24h']+row['remaining_work_kg_at_24h'],
                         row['cargo_quantity_kg'])
        self.assertEqual(row['completed_work_kg_at_72h']+row['remaining_work_kg_at_72h'],
                         row['cargo_quantity_kg'])

    def test_completed_history_unchanged_by_future_shock(self):
        snap,candidate=ongoing_fixture()
        snap['as_of_at']='2026-11-02T04:00:00+09:00'
        snap['call_states'][0].update(status='COMPLETED',remaining_work_kg=0)
        original=copy.deepcopy(candidate['vessel_results'][0])
        from weather_optimizer import WeatherSeries
        weather=WeatherSeries.from_csv(Path(snap['weather']['path']))
        overlay=scenario_weather(weather,dict(weather_blocks=[
            {'onset_offset_minutes':10,'duration_minutes':60}]),
            local_time(snap['as_of_at']))
        row=_physical_replay(snap,candidate,
            {'RISK-001':{'available_at_shift_minutes':300,
                         'work_duration_multiplier':2}},overlay)[0]
        self.assertEqual(row['realized_start_at'],original['work_start_at'])
        self.assertEqual(row['realized_completion_at'],original['completion_at'])
        self.assertEqual(row['remaining_work_kg_at_as_of'],0)

    def test_predecessor_cause_requires_counterfactual_start_delay(self):
        snap,candidate=fixture()
        result=evaluate_scenario(snap,candidate,dict(scenario_id='independent-arrival',
            call_shocks={'RISK-001':{'work_duration_multiplier':2},
                         'RISK-002':{'available_at_shift_minutes':180}}))
        self.assertEqual(result['physical_status'],'FEASIBLE')
        second=next(r for r in result['actual_schedules'] if r['call_id']=='RISK-002')
        self.assertEqual(second['predecessor_delay_seconds'],0)
        self.assertNotIn('RISK-002',result['propagated_delay_call_ids'])

    def test_rounding_sensitivity_is_isolated_from_primary_policy(self):
        base=datetime(2026,11,2,10)
        for seconds in (1,29,30,59):
            at=base+timedelta(seconds=seconds)
            self.assertEqual(project_minute_boundary(at,'FLOOR').minute,0)
            self.assertEqual(project_minute_boundary(at,'CEILING').minute,1)
            self.assertEqual(project_minute_boundary(at,'ROUND_HALF_UP').minute,
                             1 if seconds>=30 else 0)
        amounts={}
        finish=base+timedelta(minutes=1,seconds=30)
        for mode in ('FLOOR','CEILING','ROUND_HALF_UP'):
            projected=project_minute_boundary(finish,mode).replace(tzinfo=None)
            amounts[mode]=settle_intervals(contract(daily_demurrage_usd='1440'),
                base,projected,[(base,projected,'WAIT')])['demurrage_cents']
        self.assertEqual(amounts,{'FLOOR':100,'CEILING':200,'ROUND_HALF_UP':200})
        snap,candidate=fixture()
        with self.assertRaisesRegex(ValueError,'Unsupported Risk settlement policy'):
            evaluate_scenario(snap,candidate,dict(scenario_id='x'),
                              settlement_policy='SIMULATION_MINUTE_BOUNDARY_V1')


if __name__=='__main__':
    unittest.main()
