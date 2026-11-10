import json
import subprocess
import sys
import unittest

class InventoryRelaxationTest(unittest.TestCase):
    def test_false_infeasible_heuristic_falls_back_to_exact_search(self):
        code='''
import json
import pyomo.environ as pyo
from tests.test_v3_tank_inventory import V3InventoryTests
from src.daily_planning.tank import v3_inventory_relaxation as relax
from src.daily_planning.tank.v3_optimization import _solve_v3_allocation
f=V3InventoryTests();f.setUp();row=dict(f.x[0]);row.pop("tank_id");row["eligible_tank_ids"]=["T"]
def bad(model,*args,**kwargs):
 model.inventory_heuristic=pyo.ConstraintList();model.inventory_heuristic.add(model.y[0,"T"]<=-1);return 1
relax.add_inventory_relaxation=bad
r=_solve_v3_allocation(f.s,[row],start_at="2026-11-03T00:00:00+09:00",end_at="2026-11-04T01:00:00+09:00",inventory_relaxation=True,time_limit_seconds=15)
print(json.dumps({"status":r["status"],"tank":r.get("assignments",[{}])[0].get("tank_id")}))
'''
        run=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=20,check=True)
        self.assertEqual(json.loads(run.stdout),{'status':'VALID','tank':'T'})
