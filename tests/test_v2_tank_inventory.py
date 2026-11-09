"""Independent Tank V1 regression tests: run without PostgreSQL or Pyomo."""
from __future__ import annotations

import copy
import json
from fractions import Fraction
from pathlib import Path
import unittest

from src.daily_planning.tank import project_inventory, validate_network, validate_schedule

FOLDER = Path(__file__).resolve().parent / 'fixtures/tank_v1'


def fixtures():
    network = json.loads((FOLDER / 'synthetic_network_30tanks.json').read_text(encoding='utf-8'))
    schedule = json.loads((FOLDER / 'synthetic_schedule.json').read_text(encoding='utf-8'))
    return network, schedule


class TankV1Tests(unittest.TestCase):
    def test_30_tanks_total_ten_million_and_read_only(self):
        network, schedule = fixtures()
        before = copy.deepcopy((network, schedule))
        index = validate_network(network)
        self.assertEqual(len(index['tank_by_id']), 30)
        result = project_inventory(network, schedule)
        self.assertEqual(result['status'], 'FEASIBLE')
        self.assertEqual(result['grid_points_per_tank'], 49)
        self.assertEqual(result['mass_verified_call_count'], 2)
        self.assertFalse(result['operational_approval'])
        self.assertEqual((network, schedule), before)

    def test_exact_final_inventory_with_fixed_outflow(self):
        network, schedule = fixtures()
        result = project_inventory(network, schedule)
        self.assertEqual(result['inventory_at_grid']['TANK-01'][-1]['exact_bbl'], '506000')
        self.assertEqual(result['inventory_at_grid']['TANK-04'][-1]['exact_bbl'], '180000')
        self.assertEqual(result['inventory_at_grid']['TANK-02'][-1]['exact_bbl'], '500000')

    def test_missing_connection_is_infeasible(self):
        network, schedule = fixtures()
        schedule['calls'][0]['berth_id'] = 'UNKNOWN-BERTH'
        result = project_inventory(network, schedule)
        self.assertEqual(result['status'], 'INFEASIBLE')
        self.assertIn('TANK_CONNECTION_UNAVAILABLE', {v['code'] for v in result['issues']})

    def test_cargo_type_incompatibility_is_infeasible(self):
        network, schedule = fixtures()
        schedule['calls'][0]['cargo_type'] = 'LPG'
        self.assertIn('TANK_CARGO_INCOMPATIBLE', {v['code'] for v in project_inventory(network,schedule)['issues']})

    def test_overflow_detected_at_subgrid_event_boundary(self):
        network, schedule = fixtures()
        network['tanks'][0]['initial_stock_bbl'] = '995000'
        schedule['calls'] = [schedule['calls'][0]]
        schedule['calls'][0]['cargo_volume_bbl'] = '10000'
        schedule['calls'][0]['cargo_mass_kg'] = '1200000'
        schedule['segments'] = [{'call_id':'TEST-CALL-A',
            'start_at':'2026-11-09T08:10:00+09:00',
            'end_at':'2026-11-09T08:30:00+09:00',
            'rate_bbl_per_hour':'30000'}]
        result = project_inventory(network, schedule)
        self.assertEqual(result['status'], 'INFEASIBLE')
        self.assertIn('TANK_CAPACITY_EXCEEDED', {v['code'] for v in result['issues']})

    def test_capacity_violation_mid_grid_even_if_end_stock_recovers(self):
        network, schedule = fixtures()
        tank = network['tanks'][0]
        tank['initial_stock_bbl'] = '999000'
        tank['fixed_process_net_flow_bbl_per_hour'] = '-12000'
        schedule['calls'] = [schedule['calls'][0]]
        schedule['calls'][0]['cargo_volume_bbl'] = '6000'
        schedule['calls'][0]['cargo_mass_kg'] = '720000'
        schedule['segments'] = [{'call_id':'TEST-CALL-A',
            'start_at':'2026-11-09T08:00:00+09:00',
            'end_at':'2026-11-09T08:12:00+09:00',
            'rate_bbl_per_hour':'30000'}]
        # Net +18k bbl/h during transfer, crossing capacity before 08:12;
        # subsequent process flow restores 08:30 stock below capacity.
        result = project_inventory(network, schedule)
        self.assertEqual(result['status'],'INFEASIBLE')
        self.assertIn('TANK_CAPACITY_EXCEEDED', {v['code'] for v in result['issues']})
        self.assertLess(Fraction(result['inventory_at_grid']['TANK-01'][1]['exact_bbl']), 1_000_000)

    def test_single_tank_cargo_not_allowed_to_disappear(self):
        network, schedule = fixtures()
        schedule['segments'][0]['end_at'] = '2026-11-09T08:30:00+09:00'
        self.assertIn('CARGO_VOLUME_MISMATCH', {v['code'] for v in project_inventory(network,schedule)['issues']})

    def test_mass_requires_source_density_and_exact_matching(self):
        network, schedule = fixtures()
        schedule['calls'][0].pop('density_kg_per_bbl')
        self.assertIn('MISSING_MASS_CONVERSION', {v['code'] for v in project_inventory(network,schedule)['issues']})
        _, schedule = fixtures()
        schedule['calls'][0]['density_kg_per_bbl'] = '119'
        self.assertIn('MASS_VOLUME_MISMATCH', {v['code'] for v in project_inventory(network,schedule)['issues']})

    def test_no_unsourced_float_volume_and_no_wrong_total_capacity(self):
        network, schedule = fixtures()
        network['tanks'][0]['capacity_bbl'] = 1000000.0
        with self.assertRaises(ValueError): validate_network(network)
        network, schedule = fixtures()
        network['tanks'][0]['capacity_bbl'] = '999999'
        with self.assertRaises(ValueError): validate_network(network)

    def test_a_second_precision_event_preserved_without_rounding(self):
        network, schedule = fixtures()
        schedule['calls'] = [schedule['calls'][0]]
        schedule['calls'][0]['cargo_volume_bbl'] = '1'
        schedule['calls'][0]['cargo_mass_kg'] = '120'
        schedule['segments'] = [{'call_id': 'TEST-CALL-A',
            'start_at':'2026-11-09T08:00:00+09:00',
            'end_at':'2026-11-09T08:00:01+09:00',
            'rate_bbl_per_hour':'3600'}]
        result = project_inventory(network, schedule)
        self.assertEqual(result['status'], 'FEASIBLE')
        point = next(x for x in result['inventory_at_events']['TANK-01'] if x['at'].endswith('08:00:01+09:00'))
        self.assertEqual(Fraction(point['exact_bbl']), Fraction(500000) + 1 - Fraction(1000,3600))

    def test_shared_berth_conflict_detected(self):
        network, schedule = fixtures()
        # Explicitly provide another compatible path on same berth with the same tank.
        schedule['calls'].append(dict(schedule['calls'][0], call_id='TEST-CALL-C'))
        schedule['segments'].append(dict(schedule['segments'][0], call_id='TEST-CALL-C'))
        result = project_inventory(network,schedule)
        self.assertIn('SHARED_RESOURCE_OVERLAP', {v['code'] for v in result['issues']})

    def test_168_hour_inventory_grid_and_72_hour_carryover(self):
        network, schedule = fixtures()
        schedule['horizon_end_at'] = '2026-11-16T08:00:00+09:00'
        schedule['calls'].append({
            'call_id': 'TEST-CROSS-72H', 'cargo_type': 'CRUDE', 'direction': 'DISCHARGE',
            'berth_id': 'TEST-CRUDE-1', 'tank_id': 'TANK-01',
            'cargo_volume_bbl': '30000', 'cargo_mass_kg': '3600000',
            'density_kg_per_bbl': '120'})
        schedule['segments'].append({
            'call_id':'TEST-CROSS-72H',
            'start_at': '2026-11-12T07:00:00+09:00',
            'end_at': '2026-11-12T10:00:00+09:00',
            'rate_bbl_per_hour':'10000'})
        result = project_inventory(network, schedule)
        self.assertEqual(result['status'], 'FEASIBLE')
        self.assertEqual(result['grid_points_per_tank'], 337)
        points = result['inventory_at_grid']['TANK-01']
        state_72h = next(p for p in points if p['at'] == '2026-11-12T08:00:00+09:00')
        state_74h = next(p for p in points if p['at'] == '2026-11-12T10:00:00+09:00')
        self.assertEqual(Fraction(state_74h['exact_bbl'])-Fraction(state_72h['exact_bbl']),
                         Fraction(18000))  # 2h * (10k transfer - 1k fixed outflow)

    def test_nonoptional_fixed_outflow_can_make_schedule_infeasible(self):
        network, schedule = fixtures()
        network['tanks'][0]['initial_stock_bbl'] = '10000'
        network['tanks'][0]['fixed_process_net_flow_bbl_per_hour'] = '-100000'
        result = project_inventory(network, schedule)
        self.assertEqual(result['status'], 'INFEASIBLE')
        self.assertIn('TANK_STOCK_BELOW_MINIMUM', {v['code'] for v in result['issues']})

    def test_midnight_rollover_and_timezone_required(self):
        network, schedule = fixtures()
        result = project_inventory(network, schedule)
        self.assertTrue(result['inventory_at_grid']['TANK-01'][-1]['at'].endswith('2026-11-10T08:00:00+09:00'))
        schedule['horizon_start_at'] = '2026-11-09T08:00:00'
        with self.assertRaises(ValueError): validate_schedule(network, schedule)


if __name__ == '__main__':
    unittest.main()
