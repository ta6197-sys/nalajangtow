"""Run from extracted skeleton root: python examples/standalone_demo.py

Only mathematical illustration; NOT results from Ulsan CLX or PostgreSQL.
"""
import json
from src.daily_planning.risk.metrics import compare_scenario_costs
from src.daily_planning.risk.scenarios import load_scenario_config, generate_scenarios

config = load_scenario_config('config/risk_synthetic_v1.json')
config['sample_count'] = 3
scenarios = generate_scenarios(config,['DEMO-CALL-01','DEMO-CALL-02'])
report = compare_scenario_costs({
    'A': {s['scenario_id']:c for s,c in zip(scenarios,[100000,150000,250000])},
    'B': {s['scenario_id']:c for s,c in zip(scenarios,[110000,130000,210000])},
}, baseline='A')
print(json.dumps({
    'warning':'ILLUSTRATIVE DATA ONLY; NO SCHEDULE REPLAY',
    'scenario_count':len(scenarios),
    'math_example':report,
},ensure_ascii=False,indent=2))
