"""Isolated synthetic comparison of versioned finite-domain searches."""
import argparse
from collections import OrderedDict
from decimal import Decimal
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benchmarks.v2_phase0 import fixture
from src.daily_planning.exact_search import build_domain, cost_then_wait_search, exact_search
from src.daily_planning.event_candidates import LEGACY, EVENTS


def run(count, variant, budget):
    snapshot,weather=fixture(count)
    t=time.perf_counter()
    domain=build_domain(snapshot,weather,candidate_generator=LEGACY if variant=='legacy' else EVENTS)
    domain_seconds=time.perf_counter()-t
    cache=None if variant=='legacy' else OrderedDict()
    warm=variant=='events_hint'
    candidates=[]; records=[]
    for label in 'ABC':
        t=time.perf_counter()
        if label=='A': result,record=cost_then_wait_search(snapshot,weather,domain,time_limit=budget,replay_cache=cache,warm_start=warm)
        else: result,record=exact_search(snapshot,weather,domain,objective='wait',time_limit=budget,cost_cap=cap,excluded_signatures=[c['signature'] for c in candidates],replay_cache=cache,warm_start=warm)
        elapsed=time.perf_counter()-t
        if result:
            cost=result['evaluation']['summary']['demurrage_increment_to_completion_cents']
            if label=='A': cap=int(Decimal(cost)*Decimal('1.03'))
            candidates.append({'label':label,'cost_cents':cost,'signature':result['schedule_signature']})
        elif label=='A': raise RuntimeError('No A candidate')
        records.append({'label':label,'seconds':elapsed,'search':record})
    return {'calls':count,'variant':variant,'domain_seconds':domain_seconds,'options':len(domain['options']),'candidates':candidates,'records':records}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--budget',type=float,default=1);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    report={'schema':'phase1-synthetic-v1','budget_seconds_per_candidate':args.budget,'results':[run(n,v,args.budget) for n in (2,4,8) for v in ('legacy','events','events_hint')]}
    args.output.write_text(json.dumps(report,indent=2)+'\n')
if __name__=='__main__':main()
