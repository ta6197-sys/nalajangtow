import copy
import json
from pathlib import Path
import unittest

from src.daily_planning.integrated_input.normalizer import normalize
from src.daily_planning.physical_scheduling.compatibility import compatibility,berth_duration_seconds
from src.daily_planning.physical_scheduling.phases import replay
from src.daily_planning.physical_scheduling.validator import validate_result,pair_conflicts
from src.daily_planning.physical_scheduling.service import solve_berth_schedule
from src.daily_planning.physical_scheduling.pilotage import derive_windows
from src.daily_planning.physical_scheduling.policy import event_definition_issues
from src.daily_planning.physical_scheduling.user_policy import build_user_policy
import tempfile


def fixture():
    b=json.loads((Path(__file__).parent/'fixtures/integrated_input_v3/small_synthetic_bundle.json').read_text())
    s=normalize(b)
    return s['calls'][0],s['berths'][0]


class V3CompatibilityTest(unittest.TestCase):
    def test_documentary_compatibility(self):
        c,b=fixture();self.assertEqual(compatibility(c,b),[])
        self.assertGreater(berth_duration_seconds(c,b),0)
    def test_wrong_cargo_and_dwt_fail(self):
        c,b=fixture();c['cargo_group']='UNKNOWN';c['physical']['dwt_mt']='99999999'
        self.assertEqual({x['field'] for x in compatibility(c,b)},{'cargo_group','dwt_mt'})
    def test_missing_dimensions_are_not_invented(self):
        c,b=fixture();del c['physical']['loa_m']
        self.assertIn('MISSING_PHYSICAL_DATA',{x['code'] for x in compatibility(c,b)})
    def test_compatibility_does_not_change_inputs(self):
        c,b=fixture();old=copy.deepcopy((c,b));compatibility(c,b);berth_duration_seconds(c,b)
        self.assertEqual((c,b),old)




def scenario():
    c,b=fixture();c.update(call_id='SYN-A',cargo_quantity_mt='1',eta_at='2026-11-03T00:00:00+09:00',fixed_etb_is_hard=False,fix_etb_at=None)
    b.update(hose_disconnect_min='1',switching_min='1')
    window={'start_at':'2026-11-03T00:00:00+09:00','end_at':'2026-11-09T00:00:00+09:00'}
    p={'schema_version':'V3_SYNTHETIC_PHASE_POLICY_V1','data_origin':'SYNTHETIC_FIXTURE','operational_approval':False,
       'source_reference':'ISOLATED_UNIT_FIXTURE_NOT_PORT_POLICY','dock_4142_version':'DISABLED','tug_capacity':2,
       'berths':{b['berth_id']:{'berthing_seconds':60,'hose_connection_seconds':60,'unberthing_seconds':60,'tug_units':1,
                              'pilotage_windows':[dict(window)],'work_windows':[dict(window)]}}}
    s={'schema_version':'DOCKTIMIZE_NORMALIZED_INPUT_V3','calls':[c],'berths':[b],
       'workbook_tables':{'groups':{'rows':[]},'restrictions':{'rows':[]}}}
    return s,p

START='2026-11-03T00:00:00+09:00'


class V3SchedulerTest(unittest.TestCase):
    def solve(self,s,p,candidates,**kw):return solve_berth_schedule(s,policy=p,as_of_at=START,candidate_etbs=candidates,**kw)
    def test_fix_is_exact_and_preceding_nonfix_can_move(self):
        s,p=scenario();a=s['calls'][0];fixed=dict(a,call_id='SYN-FIX',fixed_etb_is_hard=True,fix_etb_at='2026-11-03T01:00:00+09:00');s['calls'].append(fixed)
        result=self.solve(s,p,{'SYN-A':['2026-11-03T01:00:00+09:00','2026-11-03T02:00:00+09:00']})
        self.assertEqual(result['status'],'BERTH_SUBSYSTEM_FEASIBLE',result)
        by={r['call_id']:r for r in result['schedule']}
        self.assertEqual(by['SYN-FIX']['etb_at'],fixed['fix_etb_at'])
        self.assertEqual(by['SYN-A']['etb_at'],'2026-11-03T02:00:00+09:00')
    def test_same_berth_cannot_overlap(self):
        s,p=scenario();s['calls'].append(dict(s['calls'][0],call_id='SYN-B'))
        r=self.solve(s,p,{cid:['2026-11-03T01:00:00+09:00'] for cid in ['SYN-A','SYN-B']})
        self.assertEqual(r['status'],'INFEASIBLE')
    def test_missing_policy_and_pilotage_window_fail_closed(self):
        s,p=scenario()
        self.assertEqual(self.solve(s,{}, {})['status'],'INVALID_INPUT')
        p['berths'][s['berths'][0]['berth_id']]['pilotage_windows']=[{'start_at':START,'end_at':'2026-11-03T00:30:00+09:00'}]
        self.assertEqual(self.solve(s,p,{'SYN-A':['2026-11-03T01:00:00+09:00']})['status'],'INFEASIBLE')
    def test_second_precision_and_full_commitment_protection(self):
        s,p=scenario();row=replay(s['calls'][0],s['berths'][0],'2026-11-03T01:00:13+09:00',p)
        before=copy.deepcopy((s,p,row))
        result=self.solve(s,p,{},commitments=[row])
        self.assertEqual((s,p,row),before)
        self.assertEqual(result['schedule'],[row]);self.assertEqual(row['etb_at'],'2026-11-03T01:00:13+09:00')
        carried=solve_berth_schedule(s,policy=p,as_of_at='2026-11-03T01:00:30+09:00',candidate_etbs={},commitments=[row])
        self.assertEqual(carried['schedule'],[row])
    def test_work_windows_split_work_and_preserve_occupation(self):
        s,p=scenario();s['calls'][0]['cargo_quantity_mt']='32'
        bid=s['berths'][0]['berth_id'];p['berths'][bid]['work_windows']=[
          {'start_at':'2026-11-03T01:01:00+09:00','end_at':'2026-11-03T01:02:00+09:00'},
          {'start_at':'2026-11-04T06:00:00+09:00','end_at':'2026-11-04T07:00:00+09:00'}]
        row=replay(s['calls'][0],s['berths'][0],'2026-11-03T01:00:00+09:00',p)
        self.assertEqual(len(row['work_intervals']),2);self.assertTrue(row['etu_at'].startswith('2026-11-04'))
    def test_restriction_blocks_cargo_without_moving_fix(self):
        s,p=scenario();s['calls'][0].update(fixed_etb_is_hard=True,fix_etb_at='2026-11-03T01:00:00+09:00')
        s['workbook_tables']['restrictions']['rows']=[{'berth_id':s['berths'][0]['berth_id'],'start_local':'2026-11-03T00:30:00+09:00','end_local':'2026-11-03T02:00:00+09:00','restriction_scope':'BOTH','cargo_group':None}]
        r=self.solve(s,p,{})
        self.assertEqual(r['status'],'INFEASIBLE');self.assertEqual(r['issues'][0]['code'],'FIX_PRECHECK_FAILED')
    def test_4142_versions_keep_overlap_conditional(self):
        s,p=scenario();a=s['calls'][0];b=s['berths'][0];b.update(hose_disconnect_min='120',switching_min='90',max_transfer_rate_mt_h='60')
        other=dict(b,berth_id='SYN-BERTH');s['berths'].append(other);s['calls'].append(dict(a,call_id='SYN-B'))
        p['berths']['SYN-BERTH']=copy.deepcopy(p['berths'][b['berth_id']]);p.update(dock_4142_version='SYNTHETIC_WORK_END_V1',dock_4142_berths=[b['berth_id'],'SYN-BERTH'],dock_4142_shared_resources={'SYN-CARGO':['CARGO']})
        s['workbook_tables']['groups']['rows']=[{'rule_id':'DOCK-4142','rule_type':'PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP','berth_ids':b['berth_id']+';SYN-BERTH'}]
        first=replay(a,b,'2026-11-03T01:00:00+09:00',p);second=replay(s['calls'][1],other,'2026-11-03T02:40:00+09:00',p)
        self.assertEqual(pair_conflicts(first,second,s,p),[])
        p['dock_4142_version']='SYNTHETIC_HOSE_END_V1'
        first=replay(a,b,'2026-11-03T01:00:00+09:00',p)
        self.assertIn('4142_SWITCHING_CONFLICT',pair_conflicts(first,second,s,p))
    def test_4142_can_wait_alongside_until_shared_hose_resource_releases(self):
        s,p=scenario();a=s['calls'][0];b=s['berths'][0];b.update(hose_disconnect_min='120',switching_min='90',max_transfer_rate_mt_h='60')
        other=dict(b,berth_id='SYN-BERTH');s['berths'].append(other)
        a.update(fixed_etb_is_hard=True,fix_etb_at='2026-11-03T01:00:00+09:00')
        s['calls'].append(dict(a,call_id='SYN-B',fix_etb_at='2026-11-03T02:40:00+09:00'))
        p['berths']['SYN-BERTH']=copy.deepcopy(p['berths'][b['berth_id']])
        p.update(dock_4142_version='SYNTHETIC_HOSE_END_V1',dock_4142_berths=[b['berth_id'],'SYN-BERTH'],
                 dock_4142_shared_resources={'SYN-SHARED':['HOSE_CONNECT','CARGO','HOSE_DISCONNECT','SWITCHING']},
                 dock_4142_berthing_resource='INDEPENDENT',legacy_etu_after_work_seconds=7200,event_definition_resolution='EXPLICIT_PHASED_SYNTHETIC_RESOLUTION')
        s['workbook_tables']['groups']['rows']=[{'rule_id':'DOCK-4142','rule_type':'PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP','berth_ids':b['berth_id']+';SYN-BERTH'}]
        result=self.solve(s,p,{})
        self.assertEqual(result['status'],'BERTH_SUBSYSTEM_FEASIBLE',result)
        rows={r['call_id']:r for r in result['schedule']}
        self.assertLess(rows['SYN-B']['etb_at'],rows['SYN-A']['etu_at'])
        self.assertGreaterEqual(rows['SYN-B']['hose_connection_start_at'],rows['SYN-A']['switching_end_at'])
        p['event_definition_resolution']='UNRESOLVED'
        self.assertEqual(self.solve(s,p,{})['status'],'UNSUPPORTED_OPERATIONAL_POLICY')

    def test_started_inside_72h_can_finish_after_horizon(self):
        s,p=scenario();s['calls'][0]['cargo_quantity_mt']='1920'
        result=self.solve(s,p,{'SYN-A':['2026-11-05T23:30:00+09:00']})
        self.assertEqual(result['status'],'BERTH_SUBSYSTEM_FEASIBLE',result)
        self.assertGreater(result['schedule'][0]['etu_at'],result['boundaries']['optimization_end_at'])

    def test_global_four_tug_capacity_rejects_three_simultaneous_heavy_vessels(self):
        s,p=scenario();base=s['calls'][0];base_berth=s['berths'][0]
        p['tug_capacity']=4
        for cfg in p['berths'].values():cfg['tug_units']=2
        for idx in (1,2):
            bid='SYN-BERTH-'+str(idx);s['berths'].append(dict(base_berth,berth_id=bid))
            p['berths'][bid]=copy.deepcopy(next(iter(p['berths'].values())))
            s['calls'].append(dict(base,call_id='SYN-'+str(idx)))
        result=self.solve(s,p,{c['call_id']:['2026-11-03T01:00:00+09:00'] for c in s['calls']})
        self.assertEqual(result['status'],'INFEASIBLE')
        s['calls'].pop()
        result=self.solve(s,p,{c['call_id']:['2026-11-03T01:00:00+09:00'] for c in s['calls']})
        self.assertEqual(result['status'],'BERTH_SUBSYSTEM_FEASIBLE',result)

    def test_tug_capacity_is_a_hard_resource(self):
        s,p=scenario();p['tug_capacity']=0
        self.assertEqual(self.solve(s,p,{'SYN-A':['2026-11-03T01:00:00+09:00']})['status'],'INFEASIBLE')

class V3PolicyEdgeTest(unittest.TestCase):
    def test_grt_threshold_last_pob_and_berth_precedence(self):
        s,p=scenario();call=s['calls'][0];day=[{'date_kst':'2026-11-03','sunrise_kst':'06:30:00','sunset_kst':'17:30:00'}]
        call['physical']['grt']='29999'
        low=derive_windows(call,day,night_permitted=True,last_pob_offset_seconds=1800,last_pob_basis='START')
        self.assertTrue(low[0]['start_at'].endswith('00:00:00+09:00'))
        call['physical']['grt']='30000'
        high=derive_windows(call,day,night_permitted=True,last_pob_offset_seconds=1800,last_pob_basis='START')
        self.assertTrue(high[0]['latest_start_at'].endswith('17:00:00+09:00'))
        call['physical']['grt']='10000'
        restricted=derive_windows(call,day,night_permitted=False,last_pob_offset_seconds=1800,last_pob_basis='COMPLETE')
        self.assertTrue(restricted[0]['end_at'].endswith('17:30:00+09:00'))
    def test_4142_double_count_conflict_is_explicit(self):
        s,p=scenario();bid=s['berths'][0]['berth_id'];s['berths'][0]['hose_disconnect_min']='120'
        p['berths'][bid]['unberthing_seconds']=3600;p.update(dock_4142_berths=[bid],legacy_etu_after_work_seconds=7200)
        s['workbook_tables']['groups']['rows']=[{'rule_type':'PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP'}]
        issues=event_definition_issues(s,p)
        self.assertEqual(issues[0]['legacy_work_end_to_etu_seconds'],7200)
        self.assertEqual(issues[0]['phased_work_end_to_etu_seconds'],10800)

class V3UserPolicyTest(unittest.TestCase):
    def test_approved_defaults_grt_tugs_and_duration_override(self):
        s,p=scenario();bid=s['berths'][0]['berth_id']
        s['calls'][0]['physical']['grt']='30000'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'daylight.csv';path.write_text('date_kst,sunrise_kst,sunset_kst\n2026-11-03,06:30,17:30\n')
            result=build_user_policy(s,path,start_at=START,end_at='2026-11-04T00:00:00+09:00',
                night_permissions={bid:{'berthing':True,'unberthing':False}},berth_overrides={bid:{'berthing_seconds':1800}})
        self.assertEqual(result['status'],'POLICY_INPUTS_VALID')
        policy=result['policy'];row=policy['berths'][bid]
        self.assertEqual(row['berthing_seconds'],1800);self.assertEqual(row['hose_connection_seconds'],7200)
        self.assertEqual(row['unberthing_seconds'],3600);self.assertEqual(row['tug_units_by_call']['SYN-A'],2)
        self.assertEqual(policy['tug_capacity'],4)
        self.assertEqual(row['pilotage_windows_by_call']['SYN-A']['berthing'][0]['latest_start_at'],'2026-11-03T16:00:00+09:00')
        self.assertEqual(row['work_windows'][0]['end_at'],'2026-11-04T00:00:00+09:00')
    def test_user_60_120_60_policy_replays_separate_boarding_and_phase_events(self):
        s,p=scenario();bid=s['berths'][0]['berth_id']
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'daylight.csv';path.write_text('date_kst,sunrise_kst,sunset_kst\n2026-11-03,06:30,17:30\n')
            result=build_user_policy(s,path,start_at=START,end_at='2026-11-04T00:00:00+09:00',night_permissions={bid:{'berthing':True,'unberthing':True}})
        row=replay(s['calls'][0],s['berths'][0],'2026-11-03T02:00:00+09:00',result['policy'])
        self.assertEqual(row['pilot_boarding_at'],'2026-11-03T01:00:00+09:00')
        self.assertEqual(row['berthing_start_at'],row['pilot_boarding_at'])
        self.assertEqual(row['work_start_at'],'2026-11-03T04:00:00+09:00')
        self.assertEqual(row['work_end_at'],'2026-11-03T04:00:04+09:00')
        self.assertEqual(row['etu_at'],'2026-11-03T05:01:04+09:00')
        self.assertEqual(row['tug_units'],{'berthing':1,'unberthing':1})

    def test_unverified_individual_night_permissions_are_pending(self):
        s,p=scenario()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'daylight.csv';path.write_text('date_kst,sunrise_kst,sunset_kst\n2026-11-03,06:30,17:30\n')
            result=build_user_policy(s,path,start_at=START,end_at='2026-11-04T00:00:00+09:00')
        self.assertEqual(result['status'],'POLICY_PENDING')
        self.assertEqual(result['issues'][0]['field'],'individual_berth_night_permissions')

if __name__=='__main__':unittest.main()
