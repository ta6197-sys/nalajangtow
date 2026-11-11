"""Resume a stopped costed simulation from its last validated synthetic boundary."""
import argparse,json
from pathlib import Path
from src.daily_planning.integrated_v3.optimizer import optimize_72h
from src.daily_planning.physical_scheduling.user_policy import build_unbounded_synthetic_policy

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('snapshot',type=Path);p.add_argument('rolling',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--time-limit',type=float,default=60);p.add_argument('--phase-release-policy',choices=('ALL_OPTIONS_V1','LOCKED_ONLY_V1'),default='ALL_OPTIONS_V1');p.add_argument('--inventory-relaxation',action='store_true');p.add_argument('--reuse-physical-model',action='store_true');p.add_argument('--berth-search-heuristic',choices=('NONE','CRUDE_INBOUND_FIRST_V1'),default='NONE');p.add_argument('--same-tank-policy',choices=('FORBID','ALLOW'),default=None);p.add_argument('--capacity-bound',action='store_true');a=p.parse_args()
    if a.output.exists():raise ValueError('Output exists')
    s=json.loads(a.snapshot.read_text());old=json.loads(a.rolling.read_text());state=old['last_simulated_state']
    if state['state_origin']!='SIMULATED_PROJECTION_NOT_OBSERVATION':raise ValueError('Synthetic state required')
    policy=build_unbounded_synthetic_policy(s,start_at=s['fixed_process_flows']['inventory_as_of_at'],end_at=s['fixed_process_flows']['flow_horizon_end_at']);policy['cargo_duration_policy']='VESSEL_BERTH_RATE_V1'
    kw={key:state[key] for key in ('completed_rows','commitments','tank_commitments','carried_transfers','initial_inventory','previous_schedule')}
    r=optimize_72h(s,as_of_at=state['boundary'],policy=policy,settlement_records=[],pricing_policy='CLEAR_WEATHER_BASELINE_V1',candidate_event_policy='V3_ETB_TENTATIVE_RELEASES_V3',inventory_cuts=True,time_limit_seconds=a.time_limit,phase_release_policy=a.phase_release_policy,inventory_relaxation=a.inventory_relaxation,reuse_physical_model=a.reuse_physical_model,berth_search_heuristic=a.berth_search_heuristic,same_tank_policy=a.same_tank_policy,**({'capacity_bound_policy':'V3_SINGLE_TANK_CAPACITY_BOUND_V1'} if a.capacity_bound else {}),**kw)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f:json.dump(r,f,ensure_ascii=False,indent=2)
    print(json.dumps({'status':r['status'],'elapsed_seconds':r['elapsed_seconds'],'iterations':len(r['iterations']),'incumbent_cost_cents':(r.get('incumbent') or {}).get('total_demurrage_cents'),'unresolved':len(r['unresolved_candidates']),'berth_issues':r.get('berth_result',{}).get('issues',[]),'output':str(a.output)},ensure_ascii=False))
    return 0 if r.get('incumbent') else 2
if __name__=='__main__':raise SystemExit(main())
