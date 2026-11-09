"""Risk CLI. --evaluate runs immutable A/B/C replay; --persist writes Risk tables only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .candidate_reader import load_run_candidates
from .scenarios import load_scenario_config, generate_scenarios
from .tradeoff import compare_candidates
from .service import evaluate_monte_carlo
from .persistence import persist_risk_analysis
from .golden_validation import load_settlement_config
from .replay import LEGACY_CONTRACT_PARITY, SECOND_PRECISION_SIMULATION_V1


def _write_new(path: Path, payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("x",encoding="utf-8") as f:
        json.dump(payload,f,ensure_ascii=False,indent=2)
        f.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id",required=True)
    parser.add_argument("--config",default="config/risk_synthetic_v1.json")
    parser.add_argument("--out")
    parser.add_argument("--save-all-scenarios",action="store_true")
    parser.add_argument("--evaluate",action="store_true")
    parser.add_argument("--sample-count",type=int,choices=(100,1000))
    parser.add_argument("--persist",action="store_true")
    parser.add_argument('--settlement-policy',choices=(LEGACY_CONTRACT_PARITY,
        SECOND_PRECISION_SIMULATION_V1),default=LEGACY_CONTRACT_PARITY)
    parser.add_argument('--settlement-config')
    args = parser.parse_args()
    if args.persist and not args.evaluate:
        parser.error('--persist requires --evaluate')
    if args.evaluate and not args.sample_count:
        parser.error('--evaluate requires --sample-count 100 or 1000')
    if not args.evaluate and not args.out:
        parser.error('Stage-1 manifest mode requires --out')
    if args.settlement_policy == SECOND_PRECISION_SIMULATION_V1 and not args.settlement_config:
        parser.error('SECOND_PRECISION_SIMULATION_V1 requires --settlement-config')
    if args.settlement_policy == LEGACY_CONTRACT_PARITY and args.settlement_config:
        parser.error('--settlement-config requires SECOND_PRECISION_SIMULATION_V1')
    from ..database import get_engine
    engine = get_engine()
    run = load_run_candidates(engine,args.run_id)
    config=load_scenario_config(args.config)
    if args.evaluate:
        config['sample_count'] = args.sample_count
        settlement_config = (load_settlement_config(args.settlement_config)
            if args.settlement_config else None)
        report = evaluate_monte_carlo(run['input_snapshot'],run['candidates'],config,
            settlement_policy=args.settlement_policy,
            settlement_config=settlement_config)
        report['run_id'] = run['run_id']
        report['scenario_config'] = config
        report['candidate_refs'] = [{key: candidate.get(key) for key in
            ('label','candidate_id','schedule_signature','freeze_signature')}
            for candidate in run['candidates']]
        analysis_id = persist_risk_analysis(engine,report) if args.persist else None
        print(json.dumps(dict(run_id=run['run_id'],analysis_id=analysis_id,
            scenario_count=report['scenario_count'],risk_status=report['risk_status'],
            coverage_by_candidate=report['coverage_by_candidate'],
            failure_breakdown_by_candidate=report['failure_breakdown_by_candidate'],
            disruption_by_candidate=report['disruption_by_candidate'],
            settlement_policy_version=report['settlement_policy_version'],
            settlement_precision=report['settlement_precision'],
            settlement_simulation_only=report['settlement_simulation_only'],
            settlement_assumptions=report['settlement_assumptions'],
            golden_validation=report['golden_validation'],
            full_distribution_available=report['full_distribution_available'],
            risk_by_candidate=report['risk_by_candidate'],
            conditional_risk_by_candidate=report['conditional_risk_by_candidate'],
            failures=[{k:v for k,v in item.items() if k != 'physical_replay'}
                      for item in report['failures']]), ensure_ascii=False))
        return
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
