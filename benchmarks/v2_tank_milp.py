"""Synthetic deterministic / LP / MILP comparison; no external state."""
import copy
import json
from pathlib import Path
import platform
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.daily_planning.tank.optimization_service import solve_tank_allocation


def run():
    network=json.loads((ROOT/'tests/fixtures/tank_v1/synthetic_network_30tanks.json').read_text())
    fixture=json.loads((ROOT/'tests/fixtures/tank_milp_v1/synthetic_requirements.json').read_text())
    records=[]
    for label in ('DETERMINISTIC','LP','MILP','MILP_SELECTION'):
        for repeat in range(3):
            windows=copy.deepcopy(fixture['transfer_windows'])
            case_network=copy.deepcopy(network); requirements=copy.deepcopy(fixture['vessel_requirements'])
            if label=='MILP_SELECTION':
                requirements[0].pop('tank_id')
                case_network['tanks'][0]['initial_stock_bbl']='1000000'
                case_network['tanks'][0]['fixed_process_net_flow_bbl_per_hour']='0'
                case_network['connections'].append({'berth_id':requirements[0]['berth_id'], 'tank_id':'TANK-02', 'max_transfer_rate_bbl_per_hour':'30000'})
            if label=='DETERMINISTIC':
                windows[0]['fixed_rate_bbl_per_hour']='30000';windows[1]['fixed_rate_bbl_per_hour']='8000'
            before=time.perf_counter()
            result=solve_tank_allocation(case_network,requirements,windows,
                horizon_start_at=fixture['horizon_start_at'],horizon_end_at=fixture['horizon_end_at'],
                mode='MILP' if label in ('MILP','MILP_SELECTION') else 'LP')
            elapsed=time.perf_counter()-before
            if result['status'] not in ('OPTIMAL','FEASIBLE'): raise RuntimeError(result)
            records.append(dict(variant=label,repeat=repeat+1,elapsed=elapsed,status=result['status'],
                mode=result['mode'],objective=result['objective'],bound=result['objective_bound'],
                numerical_validation=result['numerical_validation'],timing=result['timing'],
                input_fingerprint=result['input_fingerprint'],
                final_tank_01_exact=result['inventory']['inventory_at_grid']['TANK-01'][-1]['exact_bbl'],
                final_tank_04_exact=result['inventory']['inventory_at_grid']['TANK-04'][-1]['exact_bbl'],
                final_tank_02_exact=result['inventory']['inventory_at_grid']['TANK-02'][-1]['exact_bbl'],
                selected_assignments=result['selected_assignments']))
    import pyomo, highspy
    output={'schema':'TANK_MILP_BENCHMARK_V1','fixture':'SYNTHETIC_FIXTURE',
            'python':platform.python_version(),'pyomo':pyomo.__version__,'highs':highspy.Highs().version(),
            'note':'Same two selected tanks and windows; deterministic rates fixed, LP/MILP rates variable. MILP forced with single eligible tank per vessel; MILP_SELECTION separately adds a synthetic alternative and a full first tank.',
            'results':records}
    path=ROOT/'benchmarks/v2_tank_milp_results.json';path.write_text(json.dumps(output,indent=2)+'\n')
    for r in records: print(r['variant'],r['repeat'],r['status'],round(r['elapsed'],5),'variables',r['timing']['variable_count'],'constraints',r['timing']['constraint_count'])

if __name__=='__main__':run()
