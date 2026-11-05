"""Read-only Stage 1 CLI: validate existing A/B/C, prepare shared scenarios.

  python -m src.daily_planning.risk.cli --run-id UUID \
    --config config/risk_synthetic_v1.json --out results/risk-stage1/UUID

It generates *no* cost distribution until scenario replay is implemented.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .candidate_reader import load_run_candidates
from .scenarios import load_scenario_config, generate_scenarios
from .tradeoff import compare_candidates


def _write_new(path: Path, payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("x",encoding="utf-8") as f:
        json.dump(payload,f,ensure_ascii=False,indent=2)
        f.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id",required=True)
    parser.add_argument("--config",default="config/risk_synthetic_v1.json")
    parser.add_argument("--out",required=True)
    parser.add_argument("--save-all-scenarios",action="store_true")
    args = parser.parse_args()
    from ..database import get_engine
    engine = get_engine()
    run = load_run_candidates(engine,args.run_id)
    config=load_scenario_config(args.config)
    compared=compare_candidates(run["candidates"])
    call_ids=[v["call_id"] for v in run["candidates"][0]["vessel_results"]]
    scenarios=generate_scenarios(config,call_ids)
    out=Path(args.out)
    _write_new(out/"tradeoff.json",compared)
    _write_new(out/"scenario_manifest.json",{
        "run_id":run["run_id"],"input_snapshot_hash":run["input_snapshot_hash"],
        "scenario_count":len(scenarios),"model_id":config["model_id"],
        "provenance":config["provenance"],"risk_status":"NOT_YET_REPLAYED",
        "first_scenario_id":scenarios[0]["scenario_id"],
        "scenario_config":config,
    })
    if args.save_all_scenarios:
        _write_new(out/"scenarios.json",scenarios)
    print(f"Stage1 OK: {len(run['candidates'])} candidates, {len(scenarios)} synthetic scenarios; NO CVaR until replay")


if __name__=="__main__":
    main()
