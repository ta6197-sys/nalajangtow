"""Strict checks for the two JSON sources; unresolved evidence stays unresolved.

This module does not infer absent physical restrictions or contract source terms.
An INPUT_INCOMPLETE result must not enter the scheduler as validated input.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from fractions import Fraction
import re

from .validator import number, timestamp, tokens
from .v5 import eligible_berth_ids

SCENARIO = 'V5_BALANCED_154'
FLOW_FILE = 'Docktimize_공정유량_6h_3탱크_1000만bbl_BALANCED.json'


def validate_balanced(snapshot):
    source = snapshot['source_input']; flow = snapshot['fixed_process_flows']
    calls = snapshot['calls']; berths = snapshot['berths']; tanks = snapshot['tanks']
    profiles = source.get('vessel_profiles', []); segments = flow.get('segments', [])
    errors = []; invalid = set(); coverage = []; connections = []

    def check(condition, message, *, call_id=None, tank_id=None, category=None):
        if condition:return
        row = {'message':message}
        if call_id is not None:row['call_id']=call_id;invalid.add(str(call_id))
        if tank_id is not None:row['tank_id']=tank_id
        errors.append(row)
        if category is not None:category.append(row)

    def valid(test):
        try:return bool(test())
        except (ValueError, TypeError, KeyError, ZeroDivisionError):return False

    def unique(rows, key):
        values = [row.get(key) for row in rows]
        return all(isinstance(x,str) and x and x.strip()==x for x in values) and len(set(values))==len(values)

    for rows,key in ((calls,'call_id'),(calls,'vessel_id'),(profiles,'case_id'),
                     (profiles,'vessel_id'),(berths,'berth_id'),(tanks,'tank_id')):
        check(unique(rows,key),'Missing or duplicate '+key)
    for name,actual,expected in (('calls',len(calls),154),('profiles',len(profiles),154),
            ('berths',len(berths),11),('tanks',len(tanks),3),
            ('hard FIX',sum(c.get('fixed_etb_is_hard') is True for c in calls),15),
            ('flow records',len(segments),696)):
        check(actual==expected,f'{name}: expected {expected}, got {actual}')
    check(source.get('schema_version')=='DOCKTIMIZE_COORDINATOR_SYNTHETIC_INPUT_UNASSIGNED_V5','Unexpected input schema')
    check(flow.get('schema_version')=='FIXED_PROCESS_FLOW_TS_V1' and source.get('process_flow_schema_version')==flow.get('schema_version'),'Flow schema mismatch')
    for key in ('seed','timezone','data_origin','operational_approval'):
        check(source.get(key)==flow.get(key) and source.get(key) is not None,'Provenance mismatch: '+key)
    check(type(source.get('seed')) is int and source.get('timezone')=='Asia/Seoul' and source.get('data_origin')=='SYNTHETIC_FIXTURE' and source.get('operational_approval') is False,'Invalid synthetic origin')
    check(source.get('process_flow_file')==FLOW_FILE,'Flow filename mismatch')
    provenance=source.get('balanced_enrichment_provenance') or {}
    fingerprints={a['logical_filename']:a['sha256'] for a in snapshot.get('artifacts',[])}
    check(len(snapshot.get('artifacts',[]))==2 and set(fingerprints)=={'Docktimize_v5_선박입력_BALANCED.json',FLOW_FILE},'Expected exactly two source fingerprints')
    check(provenance.get('version')=='V5_BALANCED_154_TWO_JSON_SOURCE_ENRICHMENT_V1' and provenance.get('operational_approval') is False and provenance.get('source_workbook_sha256')==source.get('source_workbook_sha256') and provenance.get('external_flow_json_sha256')==fingerprints.get(FLOW_FILE) and isinstance(provenance.get('original_main_json_sha256'),str) and re.fullmatch(r'[0-9a-fA-F]{64}',provenance['original_main_json_sha256']) is not None,'Invalid enrichment provenance or flow fingerprint')
    check(provenance.get('source_sheets')=={'contract_terms':'03_계약조건','berth_group_rules':'15_선석그룹제약','berth_restrictions':'16_선석제약시간','legacy_policy_reference_only':'28_운영정책_4142'},'Invalid enrichment source references')
    workbook_hash=source.get('source_workbook_sha256')
    check(isinstance(workbook_hash,str) and re.fullmatch(r'[0-9a-fA-F]{64}',workbook_hash) is not None,'Invalid declared workbook fingerprint')

    tmap={t.get('tank_id'):t for t in tanks}; bmap={b.get('berth_id'):b for b in berths}
    groups={'TANK-01':'CRUDE','TANK-02':'PRODUCT','TANK-03':'LPG'}
    check(set(tmap)==set(groups),'Unexpected tank identifiers')
    group_rows=source.get('berth_group_rules'); restriction_rows=source.get('berth_restrictions')
    check(isinstance(group_rows,list) and len(group_rows)==3 and all(isinstance(r,dict) for r in group_rows),'Expected three berth group rules')
    if isinstance(group_rows,list) and all(isinstance(r,dict) for r in group_rows):
        required={'CG-678':('SAME_CARGO_NOT_CONCURRENT',{'BERTH-01','BERTH-02','BERTH-03'}),'CG-1127':('SAME_CARGO_NOT_CONCURRENT',{'BERTH-04','BERTH-05','BERTH-06'}),'DOCK-4142':('PHASE_RESOURCE_MUTEX_WITH_120MIN_OVERLAP',{'BERTH-07','BERTH-08'})}
        check(unique(group_rows,'rule_id') and {r.get('rule_id') for r in group_rows}==set(required),'Unexpected group identities')
        for r in group_rows:
            expected=required.get(r.get('rule_id'));ids=r.get('berth_id_list')
            check(bool(expected) and isinstance(ids,list) and len(set(ids))==len(ids) and set(ids)==expected[1] and set(tokens(r.get('berth_ids')))==expected[1] and r.get('rule_type')==expected[0],'Invalid berth group: '+str(r.get('rule_id')))
            if r.get('rule_id')=='DOCK-4142':check(r.get('pair_grt_exception_max')==3000 and r.get('etb_minus_prior_etu_min')==-120,'Invalid 4-1/4-2 source parameters')
    check(isinstance(restriction_rows,list) and len(restriction_rows)==17 and all(isinstance(r,dict) for r in restriction_rows),'Expected 17 berth restrictions')
    if isinstance(restriction_rows,list) and all(isinstance(r,dict) for r in restriction_rows):
        check(unique(restriction_rows,'restriction_id'),'Missing or duplicate restriction ID')
        for r in restriction_rows:
            check(r.get('berth_id') in bmap and r.get('restriction_scope') in ('BOTH','WORK','BERTHING') and r.get('reason_code') in ('CONSTRUCTION','WEATHER_DETERIORATION') and r.get('cargo_group') in (None,'','CRUDE','PRODUCT','LPG') and valid(lambda:timestamp(r['start_local'])<timestamp(r['end_local'])),'Invalid berth restriction: '+str(r.get('restriction_id')))
    for tid,t in tmap.items():
        check(t.get('cargo_group')==groups.get(tid) and valid(lambda: all(number(t[k])==v for k,v in {
            'capacity_bbl':10000000,'initial_inventory_bbl':5000000,'min_inventory_bbl':1000000,
            'max_receipt_rate_bbl_per_hour':100000,'max_dispatch_rate_bbl_per_hour':100000}.items())),
            'Invalid tank configuration',tank_id=tid)
        check(bool(tokens(t.get('connected_berth_ids'))),'Tank has no berth',tank_id=tid,category=connections)
        for bid in tokens(t.get('connected_berth_ids')):
            b=bmap.get(bid,{})
            check(tid in tokens(b.get('connected_tank_ids')) and t.get('cargo_group') in tokens(b.get('allowed_cargo_groups')),
                  'Asymmetric or cargo-incompatible berth '+str(bid),tank_id=tid,category=connections)
    for bid,b in bmap.items():
        check(b.get('active') in ('Y','N') and bool(tokens(b.get('allowed_cargo_groups'))) and bool(tokens(b.get('allowed_operations'))) and
              valid(lambda:all(number(b[k])>=0 for k in ('max_dwt_mt','min_loa_m','max_loa_m','min_pbl_m','max_draft_m','max_transfer_rate_mt_h')) and number(b['max_loa_m'])>=number(b['min_loa_m']) and number(b['max_transfer_rate_mt_h'])>0),
              'Invalid berth configuration: '+str(bid))
        if b.get('active')=='Y':check(bool(tokens(b.get('connected_tank_ids'))),'Active berth lacks tank: '+str(bid),category=connections)
        for tid in tokens(b.get('connected_tank_ids')):
            t=tmap.get(tid,{})
            check(bid in tokens(t.get('connected_berth_ids')) and t.get('cargo_group') in tokens(b.get('allowed_cargo_groups')),
                  'Asymmetric or cargo-incompatible connection: '+str(bid),tank_id=tid,category=connections)

    pmap=defaultdict(list)
    for p in profiles:pmap[p.get('case_id')].append(p)
    for c in calls:
        cid=c.get('call_id'); matches=pmap.get(cid,[]); p=matches[0] if len(matches)==1 else {}
        check(bool(p) and p.get('vessel_id')==c.get('vessel_id') and p.get('cargo_group')==c.get('cargo_group'),'Missing or inconsistent vessel profile',call_id=cid)
        check(valid(lambda:all(number(p[k])>0 for k in ('dwt_mt','loa_m','pbl_m','draft_m','grt','bbl_per_mt'))),'Invalid vessel profile',call_id=cid)
        check(valid(lambda:number(c['cargo_quantity_mt'])>0 and number(c['cargo_volume_bbl'])>0 and number(c['cargo_quantity_mt'])*number(p['bbl_per_mt'])==number(c['cargo_volume_bbl'])),'Cargo mass/volume mismatch',call_id=cid)
        check(c.get('cargo_group') in groups.values() and c.get('direction') in ('LOAD','DISCHARGE'),'Invalid cargo or direction',call_id=cid)
        check(bool(eligible_berth_ids(c,berths,tanks)),'No statically eligible connected berth',call_id=cid)
        check(valid(lambda:all(timestamp(c[k]) for k in ('eta_at','nor_tender_at','laycan_from_at','laycan_to_at')) and timestamp(c['laycan_from_at'])<=timestamp(c['laycan_to_at'])),'Invalid ETA, NOR or laycan',call_id=cid)
        check(valid(lambda:number(c['allowed_hours'])>0 and number(c['notice_hours'])>=0 and number(c['daily_demurrage_usd'])>=0) and c.get('day_mode') in ('SHINC','SHEX_SUN','SHEX_WEEKEND','SHEX_SAT12') and c.get('weather_policy') in ('COUNT','EXCLUDE','HALF') and c.get('post_demurrage_exceptions') in ('NONE','OWNER_ONLY','OWNER_AND_TERMINAL') and c.get('nor_valid') in ('Y','N') and c.get('early_start_if_work') in ('Y','N') and isinstance(c.get('contract_holidays'),list),'Invalid contract terms',call_id=cid)
        check(valid(lambda:all(date.fromisoformat(day) for day in c['contract_holidays'])),'Invalid contract holiday dates',call_id=cid)
        def contract_matches():
            allowance=number(c['allowance_value']);basis=c['laytime_basis']
            if allowance<=0 or basis not in ('MT_PER_DAY','FIXED_HOURS') or not isinstance(c.get('contract_clause_ko'),str) or not c['contract_clause_ko'].strip():return False
            exact=number(c['cargo_quantity_mt'])*24/allowance if basis=='MT_PER_DAY' else allowance
            return abs(number(c['allowed_hours'])-exact)<=Fraction(1,1000000)
        check(valid(contract_matches),'Contract allowance or clause mismatch',call_id=cid)
        hard=c.get('fixed_etb_is_hard'); fix=c.get('fix_etb_at')
        check(type(hard) is bool and ((hard and fix is not None and valid(lambda:timestamp(fix)>=timestamp(c['eta_at']))) or (hard is False and fix is None)),'Invalid FIX ETB',call_id=cid)
        for key in ('etb_at','etu_at','assigned_berth_id','assigned_tank_id','fixed_berth_id','berth_id','tank_id','sequence','vessel_sequence','work_start_at','work_end_at'):
            check(c.get(key) is None,'Unexpected preassignment: '+key,call_id=cid)
    call_ids={c.get('call_id') for c in calls}
    for p in profiles:check(p.get('case_id') in call_ids,'Profile references unknown call',call_id=p.get('case_id'))

    check(flow.get('record_count')==len(segments) and flow.get('tank_count')==len(tanks) and flow.get('interval_hours')==6,'Flow metadata mismatch',category=coverage)
    check(valid(lambda:timestamp(flow['inventory_as_of_at'])==timestamp('2026-11-03T00:00:00+09:00') and timestamp(flow['flow_horizon_end_at'])==timestamp('2026-12-31T00:00:00+09:00')),'Unexpected fixed flow horizon',category=coverage)
    by=defaultdict(list)
    for seg in segments:
        tid=seg.get('tank_id');check(tid in tmap,'Unknown flow tank',tank_id=tid,category=coverage)
        try:
            start=timestamp(seg['start_at']);end=timestamp(seg['end_at']);number(seg['fixed_net_flow_bbl_per_hour'])
            check(end-start==timedelta(hours=6),'Invalid flow duration',tank_id=tid,category=coverage)
            by[tid].append((start,end))
        except (KeyError,ValueError,TypeError,ZeroDivisionError):check(False,'Invalid flow record',tank_id=tid,category=coverage)
    for tid in tmap:
        try:
            check(len(by[tid])==232,'Unexpected flow period count',tank_id=tid,category=coverage)
            start=timestamp(flow['inventory_as_of_at']); end=timestamp(flow['flow_horizon_end_at']); cursor=start
            for a,b in sorted(by[tid]):
                check(a==cursor,'Flow gap or overlap at '+cursor.isoformat(),tank_id=tid,category=coverage);cursor=b
            check(start<end and cursor==end,'Incomplete flow horizon',tank_id=tid,category=coverage)
        except (KeyError,ValueError,TypeError):check(False,'Invalid flow horizon',tank_id=tid,category=coverage)

    pending=[{'code':'UNVERIFIED_WORKBOOK_PROVENANCE','message':'Workbook hash is a source provenance claim; workbook is not a runtime dependency.'},
             {'code':'LEGACY_DESCRIPTIVE_METADATA','message':'The source notes and historical policy references describe earlier versions; current V5 policy is separate.'}]
    return {'scenario_id':SCENARIO,'source_fingerprints':{a['logical_filename']:a['sha256'] for a in snapshot['artifacts']},
            'schema_version':source.get('schema_version'),'vessel_count':len(calls),'berth_count':len(berths),'tank_count':len(tanks),
            'hard_fix_count':sum(c.get('fixed_etb_is_hard') is True for c in calls),'ordinary_call_count':sum(c.get('fixed_etb_is_hard') is False for c in calls),
            'cargo_group_counts':dict(Counter(c.get('cargo_group') for c in calls)),
            'eligible_berth_count_by_call':{c.get('call_id'):len(eligible_berth_ids(c,berths,tanks)) for c in calls},
            'invalid_call_ids':sorted(invalid),'tank_connection_issues':connections,'flow_record_count':len(segments),'flow_coverage_issues':coverage,
            'errors':errors,'warnings':pending,'status':'INPUT_INVALID' if errors else 'OPERATIONAL_POLICY_PENDING',
            'input_status':'INPUT_INVALID' if errors else 'INPUT_VALID','data_preflight_pass':not errors}
