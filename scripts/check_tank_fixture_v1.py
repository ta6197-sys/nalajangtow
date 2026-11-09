"""Run the isolated Tank V1 feasibility example without database/network access.

Example: python scripts/check_tank_fixture_v1.py
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

# Direct execution (`python scripts/check_tank_fixture_v1.py`) adds scripts/ to
# sys.path, not the repository root. Resolve package imports explicitly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.daily_planning.tank import project_inventory


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / 'tests/fixtures/tank_v1'
    network = json.loads((folder / 'synthetic_network_30tanks.json').read_text(encoding='utf-8'))
    schedule = json.loads((folder / 'synthetic_schedule.json').read_text(encoding='utf-8'))
    result = project_inventory(network, schedule)
    print(json.dumps({
        'model_version': result['model_version'],
        'status': result['status'],
        'data_origin': result['data_origin'],
        'issue_count': len(result['issues']),
        'tank_count': len(result['inventory_at_grid']),
        'grid_points_per_tank': result['grid_points_per_tank'],
        'final_inventory_bbl': {tid: result['inventory_at_grid'][tid][-1]['bbl']
                                for tid in ('TANK-01', 'TANK-04')},
        'mass_verified_call_count': result['mass_verified_call_count'],
        'operational_approval': result['operational_approval']
    }, ensure_ascii=False, indent=2))
    if result['status'] != 'FEASIBLE':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
