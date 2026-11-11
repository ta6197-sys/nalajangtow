"""Run a small independent v5 candidate and emit a reproducible JSON report."""
import argparse
from datetime import timedelta
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import unittest
import unicodedata

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from src.daily_planning.integrated_input.validator import number,timestamp
from src.daily_planning.integrated_input.provenance import canonical,digest
from src.daily_planning.integrated_input.v5 import validate_v5
from src.daily_planning.integrated_v3.coordinator import validate_v5_candidate

TESTS=['tests.test_v5_tank_inventory','tests.test_v3_tank_inventory','tests.test_v3_carry_reconciliation','tests.test_v3_integration_checkpoint','tests.test_v5_integrated_input','tests.test_v3_integrated_input','tests.test_v3_crude_plus_input','tests.test_v3_physical_scheduling.V3CompatibilityTest']

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--snapshot',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--data-dir',type=Path,default=ROOT.parent/'data/Docktimize_v5_input');args=parser.parse_args()
    started=time.perf_counter();s=json.loads(args.snapshot.read_text(encoding='utf-8'));input_report=validate_v5(s)
    manifest=json.loads((args.snapshot.parent/'manifest.json').read_text(encoding='utf-8'))
    input_report['snapshot_hash_verified']=digest(canonical(s).encode())==manifest['normalized_snapshot_sha256']
    source_hashes={}
    for name,expected in input_report['source_fingerprints'].items():
        matches=[p for p in args.data_dir.iterdir() if p.is_file() and unicodedata.normalize('NFC',p.name)==name]
        source_hashes[name]=len(matches)==1 and hashlib.sha256(matches[0].read_bytes()).hexdigest()==expected
    if not input_report['data_preflight_pass'] or not input_report['snapshot_hash_verified'] or not all(source_hashes.values()):
        raise ValueError('Input snapshot validation or fingerprint failed')
    original_hash=hashlib.sha256(args.snapshot.read_bytes()).hexdigest();rows=[]
    for group in ('CRUDE','PRODUCT','LPG'):
        c=min((c for c in s['calls'] if c['cargo_group']==group and not c['fixed_etb_is_hard']),key=lambda c:c['eta_at'])
        b=next(b for b in s['berths'] if b['berth_id']==c['eligible_berth_ids'][0]);a=timestamp(c['eta_at']);rate=number(b['max_transfer_rate_mt_h'])*number(c['physical']['bbl_per_mt']);seconds=math.ceil(number(c['cargo_volume_bbl'])*3600/rate);z=a+timedelta(seconds=seconds)
        rows.append({'call_id':c['call_id'],'berth_id':b['berth_id'],'etb_at':a.isoformat(),'work_intervals':[[a.isoformat(),z.isoformat()]]})
    start=timestamp(s['fixed_process_flows']['inventory_as_of_at']);end=start+timedelta(hours=72)
    result=validate_v5_candidate(s,rows,as_of_at=start.isoformat(),projection_end_at=end.isoformat())
    tank=result['tank_result'];single=rows[-1];a=timestamp(single['work_intervals'][0][0]);z=timestamp(single['work_intervals'][0][1]);boundary=a+timedelta(seconds=int((z-a).total_seconds())//2)
    first=validate_v5_candidate(s,[single],as_of_at=start.isoformat(),projection_end_at=boundary.isoformat())
    state=first['tank_result']['end_state']
    second=validate_v5_candidate(s,[single],as_of_at=boundary.isoformat(),projection_end_at=end.isoformat(),initial_inventory=state['inventory_bbl'],carried_transfers=state['carried_transfers'])
    whole=validate_v5_candidate(s,[single],as_of_at=start.isoformat(),projection_end_at=end.isoformat())
    carry_ok=second['status']=='INVENTORY_VALID' and whole['status']=='INVENTORY_VALID' and second['tank_result']['end_state']['inventory_bbl']==whole['tank_result']['end_state']['inventory_bbl']
    suite=unittest.defaultTestLoader.loadTestsFromNames(TESTS)
    with open('/dev/null','w') as sink:outcome=unittest.TextTestRunner(stream=sink).run(suite)
    report={'scenario_id':'V5_3TANK','source_fingerprints':input_report['source_fingerprints'],'source_hashes_verified':source_hashes,'normalized_snapshot_sha256':manifest['normalized_snapshot_sha256'],'snapshot_file_sha256':original_hash,'input_validity':input_report['input_status'],'input_snapshot_hash_verified':input_report['snapshot_hash_verified'],'tank_count':len(s['tanks']),'inventory_validation_status':tank['status'],'full_scheduling_feasibility':'NOT_EVALUATED','physical_berth_validation':'NOT_EVALUATED','solver_bypassed':tank['solver_bypassed'],'candidate_rows':rows,'gross_vessel_rate_maxima':tank['gross_vessel_rate_maxima'],'inventory_extrema':tank['inventory_extrema'],'external_net_flow_totals_bbl':tank['external_net_flow_totals_bbl'],'quantity_reconciliation':tank['quantity_reconciliation'],'carryover_state_valid':carry_ok,'carryover_boundary':boundary.isoformat(),'issues':tank['issues'],'same_tank_concurrency_policy':'ALLOW_USER_CONFIRMED','external_flow_capacity_policy':tank['external_flow_capacity_policy'],'runtime_seconds':time.perf_counter()-started,'tests':{'suites':TESTS,'run':outcome.testsRun,'passed':outcome.testsRun-len(outcome.errors)-len(outcome.failures)-len(outcome.skipped),'failed':len(outcome.errors)+len(outcome.failures),'skipped':len(outcome.skipped),'failures':[str(t) for t,_ in outcome.failures+outcome.errors],'skips':[{'test':str(t),'reason':reason} for t,reason in outcome.skipped]}}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(args.output),'input_validity':report['input_validity'],'inventory_status':report['inventory_validation_status'],'carryover_state_valid':carry_ok,'tests':report['tests']},ensure_ascii=False,indent=2))
    return 0 if tank['status']=='VALID' and carry_ok and outcome.wasSuccessful() else 1
if __name__=='__main__':sys.exit(main())
