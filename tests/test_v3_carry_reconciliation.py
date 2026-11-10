import copy
import unittest
from src.daily_planning.integrated_v3.carryover import reconcile_projection
from src.daily_planning.tank.v3_inventory import validate_v3_inventory

class CarryReconciliationTest(unittest.TestCase):
    def setUp(self):
        from tests.test_v3_tank_inventory import V3InventoryTests
        f=V3InventoryTests();f.setUp();self.s=f.s;self.x=f.x
        self.x[0].update(start_at='2026-11-03T23:30:00+09:00',end_at='2026-11-04T00:30:00+09:00',eligible_tank_ids=['T','U'])
        self.prior=validate_v3_inventory(self.s,self.x,start_at='2026-11-03T00:00:00+09:00',end_at='2026-11-04T02:00:00+09:00')
    def test_exact_carry_replay(self):
        before=copy.deepcopy((self.s,self.prior));r=reconcile_projection(self.s,self.prior)
        self.assertEqual(r['status'],'RECONCILED',r);self.assertEqual(r['in_progress_calls'],['C']);self.assertEqual(r['next_projection']['inventories']['T'][-1]['inventory_bbl'],'140');self.assertEqual((self.s,self.prior),before);self.assertFalse(r['operational_approval'])
    def test_changed_transfer_definition_rejected(self):
        self.prior['transfer_definitions'][0]['end_at']='2026-11-04T00:30:01+09:00'
        self.assertEqual(reconcile_projection(self.s,self.prior)['status'],'REPROJECTION_FAILED')
    def test_changed_carried_inventory_detected(self):
        self.prior['carried_inventory_bbl']['T']='91'
        self.assertEqual(reconcile_projection(self.s,self.prior)['status'],'MISMATCH')
    def test_changed_source_is_rejected(self):
        self.s['tanks'][0]['capacity_bbl']='301'
        self.assertEqual(reconcile_projection(self.s,self.prior)['status'],'INVALID_INPUT')
    def test_missing_transfer_definition_fails_closed(self):
        self.prior.pop('transfer_definitions')
        self.assertEqual(reconcile_projection(self.s,self.prior)['status'],'INVALID_INPUT')
