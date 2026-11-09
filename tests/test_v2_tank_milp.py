"""Isolated synthetic LP/MILP tests; no PostgreSQL or real tank data."""
import copy
import importlib.util
from fractions import Fraction
import unittest
from unittest.mock import patch
from pathlib import Path
import json

from src.daily_planning.tank.optimization_service import solve_tank_allocation

DIR=Path(__file__).parent/'fixtures/tank_v1'
START='2026-11-09T08:00:00+09:00';END='2026-11-09T12:00:00+09:00'


def fixture():
    network=json.loads((DIR/'synthetic_network_30tanks.json').read_text())
    schedule=json.loads((DIR/'synthetic_schedule.json').read_text())
    calls=copy.deepcopy(schedule['calls'])
    windows=[{'call_id':s['call_id'],'start_at':s['start_at'],'end_at':s['end_at']} for s in schedule['segments']]
    return network,calls,windows


def solve(n,c,w,**kw):
    return solve_tank_allocation(n,c,w,horizon_start_at=START,horizon_end_at=END,**kw)


@unittest.skipUnless(importlib.util.find_spec('pyomo') and importlib.util.find_spec('highspy'), 'Optional Pyomo/HiGHS dependencies unavailable')
class TankMilpTest(unittest.TestCase):
    def test_fixed_assignment_lp_and_mass_reconciliation(self):
        n,c,w=fixture();out=solve(n,c,w)
        self.assertEqual(out['status'],'OPTIMAL',out)
        self.assertEqual(out['mode'],'LP');self.assertEqual(out['numerical_validation'],'EXACT_PASS')
        self.assertEqual(out['inventory']['mass_verified_call_count'],2)
        self.assertEqual(out['selected_assignments'],{'TEST-CALL-A':'TANK-01','TEST-CALL-B':'TANK-04'})
        self.assertFalse(out['operational_approval'])

    def test_fixed_schedule_uses_exact_simulator(self):
        n,c,w=fixture();w[0]['fixed_rate_bbl_per_hour']='30000';w[1]['fixed_rate_bbl_per_hour']='8000'
        out=solve(n,c,w)
        self.assertEqual(out['mode'],'DETERMINISTIC');self.assertEqual(out['status'],'FEASIBLE')
        self.assertEqual(out['solver_status'],'NOT_INVOKED')

    def test_selects_compatible_tank_when_other_is_full(self):
        n,c,w=fixture();c=c[:1];w=w[:1];c[0].pop('tank_id')
        n['tanks'][0]['initial_stock_bbl']='1000000';n['tanks'][0]['fixed_process_net_flow_bbl_per_hour']='0'
        n['connections'].append({'berth_id':c[0]['berth_id'],'tank_id':'TANK-02','max_transfer_rate_bbl_per_hour':'30000'})
        out=solve(n,c,w)
        self.assertEqual(out['status'],'OPTIMAL',out)
        self.assertEqual(out['mode'],'MILP');self.assertEqual(out['selected_assignments'][c[0]['call_id']],'TANK-02')

    def test_no_cargo_splitting(self):
        n,c,w=fixture();c=c[:1];w=w[:1];c[0].pop('tank_id');c[0]['cargo_volume_bbl']='50000';c[0]['cargo_mass_kg']='6000000'
        n['connections'][0]['max_transfer_rate_bbl_per_hour']='30000'
        n['connections'].append({'berth_id':c[0]['berth_id'],'tank_id':'TANK-02','max_transfer_rate_bbl_per_hour':'30000'})
        out=solve(n,c,w)
        self.assertEqual(out['status'],'INFEASIBLE',out)

    def test_missing_connection_and_incompatible_cargo(self):
        n,c,w=fixture();c[0]['berth_id']='UNMAPPED'
        self.assertEqual(solve(n,c,w)['failure_reasons'][0]['code'],'TANK_CONNECTION_UNAVAILABLE')
        n,c,w=fixture();c[0]['cargo_type']='UNKNOWN'
        self.assertEqual(solve(n,c,w)['failure_reasons'][0]['code'],'TANK_CARGO_INCOMPATIBLE')

    def test_outflow_is_fixed_and_material_balances_exact(self):
        n,c,w=fixture();out=solve(n,c,w)
        self.assertEqual(out['status'],'OPTIMAL',out)
        self.assertEqual(Fraction(out['inventory']['inventory_at_grid']['TANK-01'][-1]['exact_bbl']),526000)
        self.assertEqual(Fraction(out['inventory']['inventory_at_grid']['TANK-04'][-1]['exact_bbl']),190000)

    def test_carryover_conservation(self):
        n,c,w=fixture();c=c[:1];w=w[:1];c[0]['carried_volume_bbl']='10000'
        out=solve(n,c,w)
        self.assertEqual(out['status'],'OPTIMAL',out)
        self.assertEqual(Fraction(out['schedule']['calls'][0]['cargo_volume_bbl']),20000)
        self.assertEqual(Fraction(out['schedule']['calls'][0]['cargo_mass_kg']),2400000)

    def test_missing_density_and_bad_quantity(self):
        n,c,w=fixture();c[0].pop('density_kg_per_bbl')
        self.assertEqual(solve(n,c,w)['failure_reasons'][0]['code'],'MISSING_MASS_CONVERSION')
        n,c,w=fixture();c[0]['cargo_volume_bbl']=1.5
        self.assertEqual(solve(n,c,w)['status'],'INVALID_INPUT')

    def test_shared_berth_overlap_rejected_or_scheduled_with_milp(self):
        n,c,w=fixture();c[1]=dict(c[0],call_id='ANOTHER',cargo_volume_bbl='30000',cargo_mass_kg='3600000');w[1]=dict(w[0],call_id='ANOTHER')
        out=solve(n,c,w,mode='LP');self.assertEqual(out['failure_reasons'][0]['code'],'UNSUPPORTED_SHARED_RESOURCE_SEQUENCE')
        out=solve(n,c,w,mode='MILP');self.assertEqual(out['status'],'INFEASIBLE',out)

    def test_subgrid_availability_boundary_is_preserved(self):
        n,c,w=fixture();c=c[:1];w=w[:1];c[0]['cargo_volume_bbl']='100';c[0]['cargo_mass_kg']='12000'
        w[0].update(start_at='2026-11-09T08:07:13+09:00',end_at='2026-11-09T08:27:13+09:00')
        out=solve(n,c,w)
        self.assertEqual(out['status'],'OPTIMAL',out)
        self.assertEqual(out['schedule']['segments'][0]['start_at'],w[0]['start_at'])

    def test_both_tanks_full_is_infeasible(self):
        n,c,w=fixture();c=c[:1];w=w[:1];c[0].pop('tank_id')
        for tank in n['tanks'][:2]:
            tank['initial_stock_bbl']=tank['max_operating_bbl'];tank['fixed_process_net_flow_bbl_per_hour']='0'
        n['connections'].append({'berth_id':c[0]['berth_id'],'tank_id':'TANK-02','max_transfer_rate_bbl_per_hour':'30000'})
        self.assertEqual(solve(n,c,w)['status'],'INFEASIBLE')

    def test_fixed_rate_over_connection_limit_fails(self):
        n,c,w=fixture();w[0]['fixed_rate_bbl_per_hour']='60000';w[1]['fixed_rate_bbl_per_hour']='8000'
        result=solve(n,c,w)
        self.assertEqual(result['status'],'INFEASIBLE')
        self.assertIn('TRANSFER_RATE_EXCEEDED',{i['code'] for i in result['failure_reasons']})

    def test_subgrid_overflow_with_recovery_is_rejected(self):
        n,c,w=fixture();c=c[:1];w=w[:1];n['tanks'][0]['initial_stock_bbl']='999000';n['tanks'][0]['fixed_process_net_flow_bbl_per_hour']='-12000'
        c[0]['cargo_volume_bbl']='6000';c[0]['cargo_mass_kg']='720000'
        w[0].update(end_at='2026-11-09T08:12:00+09:00',fixed_rate_bbl_per_hour='30000')
        result=solve(n,c,w)
        self.assertEqual(result['status'],'INFEASIBLE')
        self.assertIn('TANK_CAPACITY_EXCEEDED',{i['code'] for i in result['failure_reasons']})

    def test_work_can_cross_72_hour_boundary(self):
        n,c,w=fixture();c=c[:1];w=w[:1];n['tanks'][0]['fixed_process_net_flow_bbl_per_hour']='0'
        w[0].update(start_at='2026-11-12T07:30:00+09:00',end_at='2026-11-12T08:30:00+09:00')
        result=solve_tank_allocation(n,c,w,horizon_start_at=START,horizon_end_at='2026-11-12T10:00:00+09:00')
        self.assertEqual(result['status'],'OPTIMAL',str(result.get('failure_reasons')))
        self.assertEqual(result['schedule']['segments'][-1]['end_at'],'2026-11-12T08:30:00+09:00')

    def test_solver_output_rejected_when_exact_replay_fails(self):
        n,c,w=fixture()
        from src.daily_planning.tank import optimization_service as service
        original=service._schedule
        def corrupted(*args):
            schedule,reconciliation=original(*args)
            schedule['segments'][0]['rate_bbl_per_hour']='0.001'
            return schedule,reconciliation
        with patch.object(service,'_schedule',side_effect=corrupted):
            result=solve(n,c,w)
        self.assertEqual(result['status'],'NUMERICAL_VALIDATION_FAILED')
        self.assertNotIn('schedule',result)

    def test_absent_tank_and_conversion_data_fail_closed(self):
        n,c,w=fixture();del n['tanks'][0]['initial_stock_bbl']
        self.assertEqual(solve(n,c,w)['status'],'MISSING_PHYSICAL_DATA')
        n,c,w=fixture();del c[0]['cargo_volume_bbl'];del c[0]['density_kg_per_bbl']
        self.assertEqual(solve(n,c,w)['failure_reasons'][0]['code'],'MISSING_VOLUME_OR_CONVERSION')

    def test_fixed_rate_milp_selects_tank_without_changing_rate(self):
        n,c,w=fixture();c=c[:1];w=w[:1];c[0].pop('tank_id');w[0]['fixed_rate_bbl_per_hour']='30000'
        n['tanks'][0]['initial_stock_bbl']='1000000';n['tanks'][0]['fixed_process_net_flow_bbl_per_hour']='0'
        n['connections'].append({'berth_id':c[0]['berth_id'],'tank_id':'TANK-02','max_transfer_rate_bbl_per_hour':'30000'})
        result=solve(n,c,w)
        self.assertEqual(result['status'],'OPTIMAL',str(result.get('failure_reasons')))
        self.assertEqual(result['mode'],'MILP')
        self.assertEqual(result['selected_assignments'][c[0]['call_id']],'TANK-02')
        self.assertEqual(result['schedule']['segments'][0]['rate_bbl_per_hour'],'30000')

    def test_mixed_fixed_and_variable_windows_preserve_fixed_rate(self):
        n,c,w=fixture();c=c[:1]
        w=[{'call_id':c[0]['call_id'],'start_at':'2026-11-09T08:00:00+09:00',
            'end_at':'2026-11-09T08:30:00+09:00','fixed_rate_bbl_per_hour':'20000'},
           {'call_id':c[0]['call_id'],'start_at':'2026-11-09T08:30:00+09:00',
            'end_at':'2026-11-09T09:00:00+09:00'}]
        result=solve(n,c,w)
        self.assertEqual(result['status'],'OPTIMAL',str(result.get('failure_reasons')))
        self.assertEqual(result['schedule']['segments'][0]['rate_bbl_per_hour'],'20000')
        self.assertEqual(result['schedule']['segments'][1]['rate_bbl_per_hour'],'40000')

    def test_tiny_fixed_rate_is_never_dropped_by_solver_tolerance(self):
        n,c,w=fixture();c=c[:1];c[0]['cargo_volume_bbl']='20000';c[0]['cargo_mass_kg']='2400000'
        w=[{'call_id':c[0]['call_id'],'start_at':'2026-11-09T08:00:00+09:00',
            'end_at':'2026-11-09T08:30:00+09:00','fixed_rate_bbl_per_hour':'0.000000001'},
           {'call_id':c[0]['call_id'],'start_at':'2026-11-09T08:30:00+09:00',
            'end_at':'2026-11-09T09:00:00+09:00'}]
        result=solve(n,c,w)
        self.assertEqual(result['status'],'OPTIMAL',str(result.get('failure_reasons')))
        self.assertEqual(result['schedule']['segments'][0]['rate_bbl_per_hour'],'0.000000001')

    def test_carried_work_requires_original_tank(self):
        n,c,w=fixture();c[0]['carried_volume_bbl']='1000';del c[0]['tank_id']
        self.assertEqual(solve(n,c,w)['failure_reasons'][0]['code'],'MISSING_CARRIED_TANK')

    def test_time_limit_without_incumbent_is_not_infeasible(self):
        n,c,w=fixture()
        out=solve(n,c,w,time_limit_seconds=1e-12)
        self.assertEqual(out['status'],'TIME_LIMIT_NO_INCUMBENT')
        self.assertEqual(out['solver_status'],'maxTimeLimit')
        self.assertNotIn('schedule',out)

    def test_unknown_pipeline_constraints_are_not_silently_ignored(self):
        n,c,w=fixture();n['connections'][0]['shared_pipeline_id']='P-EXAMPLE'
        out=solve(n,c,w)
        self.assertEqual(out['status'],'MISSING_PHYSICAL_DATA')
        self.assertEqual(out['failure_reasons'][0]['code'],'UNSUPPORTED_PHYSICAL_CONSTRAINT')

    def test_source_fingerprint_stable(self):
        n,c,w=fixture();before=copy.deepcopy((n,c,w));a=solve(n,c,w);b=solve(n,c,w)
        self.assertEqual((n,c,w),before)
        self.assertEqual(a['input_fingerprint'],b['input_fingerprint'])
        self.assertEqual(a['numerical_validation'],b['numerical_validation'])

if __name__=='__main__':unittest.main()
