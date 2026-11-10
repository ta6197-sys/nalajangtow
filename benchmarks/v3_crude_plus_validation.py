"""Reproduce V3_CRUDE_PLUS_3 data and limited integration evidence offline."""
import argparse
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path

from src.daily_planning.integrated_input.cli import run
from src.daily_planning.integrated_input.validator import number, timestamp
from src.daily_planning.integrated_v3.search import search_with_events
from src.daily_planning.physical_scheduling.compatibility import compatibility
from src.daily_planning.physical_scheduling.user_policy import build_unbounded_synthetic_policy
from src.daily_planning.tank.v3_inventory import validate_v3_inventory

EXPECTED={'DEM-0144':49844,'DEM-0084':90159,'DEM-0060':90892}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',default='../data');p.add_argument('--baseline',default='results/v3_preflight/670af17c193ab3d738703352/normalized_snapshot.json')
    p.add_argument('--output-dir',default='results/v3_crude_plus');p.add_argument('--time-limit',type=float,default=15)
    args=p.parse_args();report,manifest=run(args.data_dir,args.output_dir,False,scenario='V3_CRUDE_PLUS_3')
    if not report.get('data_preflight_pass'):print(json.dumps({'status':'PREFLIGHT_FAILED','validation':report},ensure_ascii=False));return 2
    modified=json.loads((Path(args.output_dir)/manifest['run_id']/'normalized_snapshot.json').read_text());baseline=json.loads(Path(args.baseline).read_text())
    old={x['call_id']:x for x in baseline['calls']};new={x['call_id']:x for x in modified['calls']};checks=[]
    def check(name,condition,details=None):checks.append({'name':name,'passed':bool(condition),'details':details})
    check('call_identity',set(old)==set(new));check('flow_unchanged',baseline['fixed_process_flows']==modified['fixed_process_flows']);check('tank_master_unchanged',baseline['tanks']==modified['tanks']);check('berth_master_unchanged',baseline['berths']==modified['berths'])
    check('SOF_unchanged',baseline['workbook_tables']['sof']==modified['workbook_tables']['sof'])
    table_changes={}
    for table in ('contracts','eta','profiles','handling','calls'):
        key='call_id' if table=='calls' else 'case_id'
        before_rows={row[key]:row for row in baseline['workbook_tables'][table]['rows']}
        after_rows={row[key]:row for row in modified['workbook_tables'][table]['rows']}
        check(table+'_row_identity',set(before_rows)==set(after_rows))
        table_changes[table]={cid:{field:[before_rows[cid].get(field),after_rows[cid].get(field)] for field in before_rows[cid].keys()|after_rows[cid].keys() if before_rows[cid].get(field)!=after_rows[cid].get(field)} for cid in before_rows.keys()&after_rows.keys() if before_rows[cid]!=after_rows[cid]}
    check('eta_reference_fields_preserved',all(set(changes)<={'quantity_mt'} for changes in table_changes['eta'].values()))
    check('contract_fields_limited',all(set(changes)<={'quantity_mt','quality_review'} for changes in table_changes['contracts'].values()))
    changed={}
    for cid in sorted(set(old)&set(new)):
        diff={k:[old[cid].get(k),new[cid].get(k)] for k in old[cid].keys()|new[cid].keys() if old[cid].get(k)!=new[cid].get(k)}
        if diff:changed[cid]=diff
    allowed={'cargo_quantity_mt','cargo_quantity_kg_exact','cargo_volume_bbl','min_work_duration_reference_berth_minutes'}
    check('only_three_calls_changed',set(changed)==set(EXPECTED));check('call_changes_expected_fields',all(set(d)<=allowed for d in changed.values()))
    check('ETA_FIX_unchanged',all((old[c]['eta_at'],old[c]['fixed_etb_is_hard'],old[c]['fix_etb_at'])==(new[c]['eta_at'],new[c]['fixed_etb_is_hard'],new[c]['fix_etb_at']) for c in old))
    source_contracts={r['case_id']:r for r in modified['workbook_tables']['contracts']['rows']};old_contracts={r['case_id']:r for r in baseline['workbook_tables']['contracts']['rows']};vessels={}
    for cid,expected in EXPECTED.items():
        c=new[cid];before=old[cid];delta=number(c['cargo_volume_bbl'])-number(before['cargo_volume_bbl']);contract=source_contracts[cid];original_contract=old_contracts[cid]
        feasible=[b['berth_id'] for b in modified['berths'] if not compatibility(c,b)]
        check(cid+'_cargo_delta',delta==expected,str(delta));check(cid+'_mass_volume',number(c['cargo_quantity_kg_exact'])==1000*number(c['cargo_quantity_mt']) and number(c['cargo_volume_bbl'])==number(c['cargo_quantity_mt'])*number(c['physical']['bbl_per_mt']))
        check(cid+'_fixed_laytime',contract['laytime_basis']==original_contract['laytime_basis']=='FIXED_HOURS' and number(contract['allowance_value'])==number(original_contract['allowance_value']) and c['allowed_hours']==before['allowed_hours'])
        check(cid+'_vessel_dwt',number(c['cargo_quantity_mt'])<=number(c['physical']['dwt_mt']));check(cid+'_compatible_berth',bool(feasible))
        vessels[cid]={'before_bbl':before['cargo_volume_bbl'],'after_bbl':c['cargo_volume_bbl'],'delta_bbl':str(delta),'before_mt':before['cargo_quantity_mt'],'after_mt':c['cargo_quantity_mt'],'after_kg_exact':c['cargo_quantity_kg_exact'],'fixed_laytime_hours':c['allowed_hours'],'compatible_berths':feasible}
    tanks=[t for t in modified['tanks'] if t['cargo_group']=='CRUDE'];ids={t['tank_id'] for t in tanks};initial=sum((number(t['initial_inventory_bbl']) for t in tanks),Fraction());minimum=sum((number(t['min_inventory_bbl']) for t in tanks),Fraction());external=sum((number(f['fixed_net_flow_bbl_per_hour'])*Fraction(int((timestamp(f['end_at'])-timestamp(f['start_at'])).total_seconds()),3600) for f in modified['fixed_process_flows']['segments'] if f['tank_id'] in ids),Fraction());unload=sum((number(c['cargo_volume_bbl']) for c in new.values() if c['cargo_group']=='CRUDE' and c['direction']=='DISCHARGE'),Fraction());load=sum((number(c['cargo_volume_bbl']) for c in new.values() if c['cargo_group']=='CRUDE' and c['direction']=='LOAD'),Fraction());ending=initial+external+unload-load
    check('aggregate_crude_ending',ending==178455,str(ending));check('aggregate_crude_minimum',minimum==178000,str(minimum))
    start=modified['fixed_process_flows']['inventory_as_of_at'];inbounds=sorted((c for c in new.values() if c['cargo_group']=='CRUDE' and c['direction']=='DISCHARGE'),key=lambda c:timestamp(c['eta_at']));earliest=inbounds[0];no_vessels=validate_v3_inventory(modified,[],start_at=start,end_at=earliest['eta_at']);crude_issues=[i for i in no_vessels.get('issues',[]) if i.get('tank_id') in ids]
    policy=build_unbounded_synthetic_policy(modified,start_at=start,end_at='2026-11-11T00:00:00+09:00');policy['cargo_duration_policy']='VESSEL_BERTH_RATE_V1'
    attempt=search_with_events(modified,as_of_at=start,policy=policy,time_limit_seconds=args.time_limit,max_iterations=5)
    tank=attempt.get('validation',{}).get('tank_result',{});issues=tank.get('issues',[]);first={}
    assignments=tank.get('assignments',[]);by_call={row['call_id']:row for row in assignments}
    for issue in issues:
        if issue.get('code')=='PENDING_SAME_TANK_CONCURRENCY':
            issue['transfer_windows']=[{'call_id':cid,'start_at':by_call[cid]['start_at'],'end_at':by_call[cid]['end_at']} for cid in issue.get('call_ids',[]) if cid in by_call]
    for issue in issues:
        key=issue['code']+':'+str(issue.get('tank_id',''))
        if key not in first:first[key]=issue
    output={'scenario':'V3_CRUDE_PLUS_3','status':'DATA_VALIDATED_SCHEDULE_UNRESOLVED' if all(c['passed'] for c in checks) else 'DATA_COMPARISON_FAILED','manifest':manifest,'preflight':report,'checks':checks,'modified_normalized_call_fields':changed,'modified_workbook_fields':table_changes,'modified_vessels':vessels,'crude_balance_bbl':{'initial':str(initial),'signed_external_net_flow':str(external),'vessel_discharge':str(unload),'vessel_load':str(load),'ending':str(ending),'minimum':str(minimum),'margin':str(ending-minimum)},'before_first_crude_discharge_eta':{'call_id':earliest['call_id'],'eta_at':earliest['eta_at'],'calculation':'NO_VESSEL_MOVEMENTS_COUNTERFACTUAL_NOT_A_SCHEDULE','crude_tank_violation_count':len(crude_issues)},'integrated_72h_attempt':{'status':attempt['status'],'iterations':attempt.get('iterations',[]),'tank_status':tank.get('status'),'issue_counts':dict(Counter(i['code'] for i in issues)),'first_issues':list(first.values()),'candidate_etbs':attempt.get('candidate_generation',{}).get('candidate_etbs',{}),'assumptions':'SYNTHETIC_PHASE_POLICY; SAME_TANK_CONCURRENCY_UNRESOLVED; FINITE_EVENT_ETBS'},'pending':['FULL_PERIOD_INTEGRATED_SCHEDULE','TANK_CONCURRENCY_POLICY','AUTHORITATIVE_SETTLEMENT_AND_WEATHER','ROLLING_STATE_CARRYOVER_IN_FULL_SCENARIO','HISTORICAL_LABEL_SHEETS_OUTSIDE_NORMALIZED_SOURCE_NOT_COMPARED'],'operational_approval':False}
    output_file=Path(args.output_dir)/manifest['run_id']/'scenario_validation_report.json'
    serialized=json.dumps(output,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    if output_file.exists():
        if output_file.read_text()!=serialized:raise ValueError('Existing evidence differs; choose a new output directory')
    else:output_file.write_text(serialized)
    print(json.dumps({'status':output['status'],'run_id':manifest['run_id'],'output_file':str(output_file),'checks_failed':[c for c in checks if not c['passed']],'balance':output['crude_balance_bbl'],'integrated_72h_attempt':output['integrated_72h_attempt']},ensure_ascii=False,indent=2))
    return 0 if output['status']=='DATA_VALIDATED_SCHEDULE_UNRESOLVED' else 2

if __name__=='__main__':raise SystemExit(main())
