"""V5 input-only normalization and integrity checks; no schedule is inferred."""
import copy
from collections import Counter, defaultdict
from datetime import timedelta
from fractions import Fraction
from .validator import number, timestamp, tokens
from ..physical_scheduling.compatibility import compatibility


def eligible_berth_ids(call, berths, tanks):
    """Return static compatibility only, including bidirectional cargo connectivity."""
    by_id = {t.get('tank_id'): t for t in tanks}
    return [b['berth_id'] for b in berths if not compatibility(call, b) and any(
        tid in by_id and by_id[tid].get('cargo_group') == call.get('cargo_group')
        and b.get('berth_id') in tokens(by_id[tid].get('connected_berth_ids'))
        for tid in tokens(b.get('connected_tank_ids')))]


def normalize_v5(bundle):
    source = copy.deepcopy(bundle['source'])
    profiles = defaultdict(list)
    for p in source['vessel_profiles']:
        profiles[p.get('case_id')].append(p)
    calls = copy.deepcopy(source['cargo_calls'])
    for call in calls:
        matches = profiles.get(call.get('call_id'), [])
        call['physical'] = copy.deepcopy(matches[0]) if len(matches) == 1 else None
        call['eligible_berth_ids'] = eligible_berth_ids(call, source['berths'], source['tanks'])
    scenario=bundle.get('scenario_id','V5_3TANK')
    tables=copy.deepcopy(bundle['tables'])
    tanks=copy.deepcopy(source['tanks'])
    if scenario=='V5_BALANCED_154':
        tables={'groups':{'sheet':'JSON:berth_group_rules','rows':copy.deepcopy(source.get('berth_group_rules',[]))},
                'restrictions':{'sheet':'JSON:berth_restrictions','rows':copy.deepcopy(source.get('berth_restrictions',[]))},
                'contracts':{'sheet':'JSON:cargo_calls','rows':[]}}
        for c in source['cargo_calls']:
            row=copy.deepcopy(c);row.update(case_id=c['call_id'],quantity_mt=c.get('cargo_quantity_mt'),nor_tender_local=c.get('nor_tender_at'),demurrage_usd_day=c.get('daily_demurrage_usd'))
            tables['contracts']['rows'].append(row)
        for t in tanks:t['effective_max_inventory_bbl']=str(number(t['capacity_bbl'])*95/100)
    return {'schema_version':'DOCKTIMIZE_NORMALIZED_INPUT_V5', 'scenario_id':scenario,
            'source_input':source, 'calls':calls, 'berths':copy.deepcopy(source['berths']),
            'tanks':tanks, 'fixed_process_flows':copy.deepcopy(bundle['flows']),
            'workbook_tables':tables, 'artifacts':copy.deepcopy(bundle['artifacts']),
            **({'source_profile_id':bundle['source_profile_id']} if bundle.get('source_profile_id') else {}),
            'units':{'tank_flow':'bbl/hour','timezone':'Asia/Seoul'}, 'operational_approval':False}


def validate_v5(s):
    if s.get('scenario_id') == 'V5_BALANCED_154':
        from .v5_balanced import validate_balanced
        return validate_balanced(s)
    source=s['source_input']; flow=s['fixed_process_flows']; tables=s['workbook_tables']
    calls=s['calls']; berths=s['berths']; tanks=s['tanks']; segments=flow.get('segments',[])
    errors=[]; connection=[]; coverage=[]; invalid=set()
    def error(message, call_id=None, tank_id=None, target=None):
        item={'message':message}
        if call_id is not None:item['call_id']=call_id;invalid.add(call_id)
        if tank_id is not None:item['tank_id']=tank_id
        errors.append(item)
        if target is not None:target.append(item)
    def check(condition, message, **kw):
        if not condition:error(message, **kw)
    def unique(rows,key,label):
        vals=[r.get(key) for r in rows]; seen=set()
        for v in vals:
            if not isinstance(v,str) or not v or v in seen:error('Missing or duplicate '+label+': '+str(v),call_id=v if label in ('call_id','profile call_id') else None)
            seen.add(v)
    def equal_num(a,b):
        try:return number(a)==number(b)
        except (ValueError,TypeError,ZeroDivisionError):return False
    def same_time(a,b):
        try:return timestamp(a)==timestamp(b)
        except (ValueError,TypeError):return False
    def numeric_valid(fn):
        try:return bool(fn())
        except (ValueError,TypeError,KeyError,ZeroDivisionError):return False
    for rows,key,label in [(calls,'call_id','call_id'),(calls,'vessel_id','vessel_id'),(source.get('vessel_profiles',[]),'case_id','profile call_id'),(source.get('vessel_profiles',[]),'vessel_id','profile vessel_id'),(berths,'berth_id','berth_id'),(tanks,'tank_id','tank_id')]:unique(rows,key,label)
    for name,count,expected in [('calls',len(calls),140),('berths',len(berths),11),('tanks',len(tanks),3),('hard FIX calls',sum(c.get('fixed_etb_is_hard') is True for c in calls),15),('flow records',len(segments),696)]:check(count==expected,f'{name}: expected {expected}, got {count}')
    check(source.get('schema_version')=='DOCKTIMIZE_COORDINATOR_SYNTHETIC_INPUT_UNASSIGNED_V5','Unexpected input schema')
    check(flow.get('schema_version')=='FIXED_PROCESS_FLOW_TS_V1' and source.get('process_flow_schema_version')==flow.get('schema_version'),'Flow schema mismatch')
    for field in ('seed','timezone','data_origin','operational_approval'):
        check(source.get(field)==flow.get(field) and source.get(field) is not None,'Provenance mismatch: '+field)
    check(source.get('timezone')=='Asia/Seoul' and source.get('data_origin')=='SYNTHETIC_FIXTURE' and source.get('operational_approval') is False,'Invalid v5 provenance')
    check(type(source.get('seed')) is int,'Invalid source seed')
    check(source.get('source_workbook_sha256')==s['artifacts'][0]['sha256'],'Workbook SHA-256 differs from JSON provenance')
    check(source.get('process_flow_file')==s['artifacts'][2]['logical_filename'],'Flow filename mismatch')
    for name in ('sof','labels'):
        check(not tables[name]['rows'],name+' contains unexpected historical records')
    tankmap={t.get('tank_id'):t for t in tanks}; berthmap={b.get('berth_id'):b for b in berths}
    expected_groups={'TANK-01':'CRUDE','TANK-02':'PRODUCT','TANK-03':'LPG'}
    check(set(tankmap)==set(expected_groups),'Unexpected v5 tank identifiers')
    for t in tanks:
        tid=t.get('tank_id');check(bool(t.get('cargo_group')) and numeric_valid(lambda: 0<=number(t['min_inventory_bbl'])<=number(t['initial_inventory_bbl'])<=number(t['capacity_bbl']) and number(t['capacity_bbl'])>0 and number(t['max_receipt_rate_bbl_per_hour'])>0 and number(t['max_dispatch_rate_bbl_per_hour'])>0),'Invalid tank cargo, inventory, capacity or rates',tank_id=tid)
        check(t.get('cargo_group')==expected_groups.get(tid) and all(equal_num(t.get(k),v) for k,v in {'capacity_bbl':10000000,'initial_inventory_bbl':5000000,'min_inventory_bbl':1000000,'max_receipt_rate_bbl_per_hour':100000,'max_dispatch_rate_bbl_per_hour':100000}.items()),'Tank differs from v5 scenario profile',tank_id=tid)
        check(bool(tokens(t.get('connected_berth_ids'))),'Tank has no connected berth',tank_id=tid,target=connection)
        for bid in tokens(t.get('connected_berth_ids')):
            b=berthmap.get(bid);check(bool(b) and tid in tokens(b.get('connected_tank_ids')) and t.get('cargo_group') in tokens(b.get('allowed_cargo_groups')),'Tank connection missing, asymmetric or wrong cargo: '+str(bid),tank_id=tid,target=connection)
    for b in berths:
        bid=b.get('berth_id'); tids=tokens(b.get('connected_tank_ids'))
        if b.get('active')=='Y':check(bool(tids),'Active berth has no tank: '+str(bid),target=connection)
        for tid in tids:
            t=tankmap.get(tid);check(bool(t) and bid in tokens(t.get('connected_berth_ids')) and t.get('cargo_group') in tokens(b.get('allowed_cargo_groups')),'Berth connection missing, asymmetric or wrong cargo: '+str(bid),tank_id=tid,target=connection)
    indices={}
    for name,key in [('contracts','case_id'),('eta','case_id'),('profiles','case_id'),('calls','call_id'),('berths','berth_id'),('tanks','tank_id')]:
        rows=[r for r in tables[name]['rows'] if r.get(key) is not None];unique(rows,key,'workbook '+name);indices[name]={r[key]:r for r in rows}
    profiles=defaultdict(list)
    for p in source.get('vessel_profiles',[]):profiles[p.get('case_id')].append(p)
    numeric_profile=('dwt_mt','loa_m','pbl_m','draft_m','grt','bbl_per_mt')
    for c in calls:
        cid=c.get('call_id'); ps=profiles.get(cid,[]);p=ps[0] if len(ps)==1 else None
        check(p is not None,'Missing or duplicate vessel profile',call_id=cid)
        if p:
            check(p.get('vessel_id')==c.get('vessel_id') and p.get('cargo_group')==c.get('cargo_group'),'Vessel profile identity mismatch',call_id=cid)
            check(all(numeric_valid(lambda k=k:number(p[k])>0) for k in numeric_profile),'Invalid vessel dimensions',call_id=cid)
            check(numeric_valid(lambda:number(c['cargo_quantity_mt'])*number(p['bbl_per_mt'])==number(c['cargo_volume_bbl'])),'Cargo mass/volume conversion mismatch',call_id=cid)
            wp=indices['profiles'].get(cid,{})
            check(wp.get('vessel_id')==p.get('vessel_id') and wp.get('cargo_group')==p.get('cargo_group') and all(equal_num(wp.get(k),p.get(k)) for k in numeric_profile),'Workbook vessel profile mismatch',call_id=cid)
        check(bool(c.get('eligible_berth_ids')),'No statically eligible berth',call_id=cid)
        hard=c.get('fixed_etb_is_hard');fix=c.get('fix_etb_at');eta=c.get('eta_at')
        check(type(hard) is bool and ((hard and bool(fix)) or (hard is False and fix is None)),'Invalid hard FIX flag or ETB',call_id=cid)
        for field in ('eta_at','nor_tender_at','laycan_from_at','laycan_to_at'):
            try:timestamp(c.get(field))
            except (ValueError,TypeError):error('Invalid '+field,call_id=cid)
        if hard and fix:
            try:check(timestamp(fix)>=timestamp(eta),'FIX ETB precedes ETA',call_id=cid)
            except (ValueError,TypeError):error('Invalid FIX ETB',call_id=cid)
        for field in ('etb_at','etu_at','assigned_berth_id','assigned_tank_id','fixed_berth_id','berth_id','tank_id','sequence','vessel_sequence','work_start_at','work_end_at'):
            if c.get(field) is not None:error('Unexpected preassignment: '+field,call_id=cid)
        wr=indices['calls'].get(cid,{})
        check(wr.get('vessel_id')==c.get('vessel_id') and wr.get('cargo_group')==c.get('cargo_group') and wr.get('direction')==c.get('direction') and same_time(wr.get('eta_local'),eta) and equal_num(wr.get('quantity_mt'),c.get('cargo_quantity_mt')) and equal_num(wr.get('cargo_volume_bbl'),c.get('cargo_volume_bbl')),'Workbook call mismatch',call_id=cid)
        check(same_time(wr.get('nor_tender_local'),c.get('nor_tender_at')) and all(wr.get(k)==c.get(k) for k in ('nor_valid','day_mode','weather_policy','post_demurrage_exceptions','early_start_if_work','laycan_scenario_kind')) and all(equal_num(wr.get(k),c.get(k)) for k in ('notice_hours','daily_demurrage_usd')) and (wr.get('fixed_etb_is_hard')=='Y')==hard and (same_time(wr.get('fix_etb_local'),fix) if hard else wr.get('fix_etb_local') is None),'Workbook call contract or FIX mismatch',call_id=cid)
        check(same_time(wr.get('laycan_from_local'),c.get('laycan_from_at')) and same_time(wr.get('laycan_to_local'),c.get('laycan_to_at')) and numeric_valid(lambda:abs(number(wr['allowed_hours'])-number(c['allowed_hours']))<=Fraction(1,1000000)),'Workbook laycan or allowed laytime mismatch',call_id=cid)
        er=indices['eta'].get(cid,{})
        check(er.get('vessel_id')==c.get('vessel_id') and same_time(er.get('eta_local'),eta) and (er.get('fixed_etb_is_hard')=='Y')==hard and (same_time(er.get('fix_etb_local'),fix) if hard else er.get('fix_etb_local') is None),'Workbook ETA/FIX mismatch',call_id=cid)
        contract=indices['contracts'].get(cid,{})
        check(contract.get('vessel_id')==c.get('vessel_id') and equal_num(contract.get('quantity_mt'),c.get('cargo_quantity_mt')) and same_time(contract.get('nor_tender_local'),c.get('nor_tender_at')) and all(equal_num(contract.get(a),c.get(b)) for a,b in [('notice_hours','notice_hours'),('demurrage_usd_day','daily_demurrage_usd')]) and all(contract.get(k)==c.get(k) for k in ('day_mode','weather_policy','post_demurrage_exceptions','nor_valid','early_start_if_work')),'Workbook contract mismatch',call_id=cid)
        def allowed():
            n=number(contract['allowance_value']);n=number(contract['quantity_mt'])*24/n if contract['laytime_basis']=='MT_PER_DAY' else n
            return number(c['allowed_hours']) in (n,Fraction(round(n*1000000),1000000))
        check(numeric_valid(allowed),'Workbook allowed laytime mismatch',call_id=cid)
    for p in source.get('vessel_profiles',[]):check(p.get('case_id') in {c.get('call_id') for c in calls},'Profile references unknown call',call_id=p.get('case_id'))
    for rows,name,key,fields in [(tanks,'tanks','tank_id',{'capacity_bbl':'capacity_bbl','initial_inventory_bbl':'initial_inventory_bbl','min_inventory_bbl':'min_inventory_bbl','max_receipt_rate_bbl_per_hour':'max_receipt_rate_bbl_h','max_dispatch_rate_bbl_per_hour':'max_dispatch_rate_bbl_h'}),(berths,'berths','berth_id',{k:k for k in ('max_dwt_mt','min_loa_m','max_loa_m','min_pbl_m','max_draft_m','max_transfer_rate_mt_h')})]:
        for r in rows:
            w=indices[name].get(r.get(key),{});check(all(equal_num(r.get(a),w.get(b)) for a,b in fields.items()) and set(tokens(r.get('connected_'+('berth' if name=='tanks' else 'tank')+'_ids')))==set(tokens(w.get('connected_'+('berth' if name=='tanks' else 'tank')+'_ids'))),'Workbook '+name+' mismatch',tank_id=r.get(key) if name=='tanks' else None)
            if name=='tanks':check(w.get('cargo_group')==r.get('cargo_group'),'Workbook tank cargo mismatch',tank_id=r.get(key))
            else:check(all(w.get(k)==r.get(k) for k in ('active','draft_check_operation')) and all(set(tokens(w.get(k)))==set(tokens(r.get(k))) for k in ('allowed_cargo_groups','allowed_operations')),'Workbook berth compatibility mismatch: '+str(r.get(key)))
    check(flow.get('record_count')==len(segments) and flow.get('tank_count')==len(tanks) and flow.get('interval_hours')==6,'Flow metadata mismatch',target=coverage)
    by=defaultdict(list); workbook_flows={}
    for w in tables['flows']['rows']:
        key=(w.get('tank_id'),w.get('start_local'),w.get('end_local'))
        if key in workbook_flows:error('Duplicate workbook flow interval',tank_id=w.get('tank_id'),target=coverage)
        workbook_flows[key]=w
    check(len(workbook_flows)==len(segments),'Workbook flow record count mismatch',target=coverage)
    for seg in segments:
        tid=seg.get('tank_id');check(tid in tankmap,'Unknown flow tank',tank_id=tid,target=coverage)
        try:
            a=timestamp(seg['start_at']);b=timestamp(seg['end_at']);check(b-a==timedelta(hours=6),'Invalid flow interval duration',tank_id=tid,target=coverage);by[tid].append((a,b))
            w=workbook_flows.get((tid,a.isoformat(),b.isoformat()),{})
            check(equal_num(w.get('fixed_net_flow_bbl_h'),seg.get('fixed_net_flow_bbl_per_hour')) and w.get('cargo_group')==tankmap.get(tid,{}).get('cargo_group'),'Workbook flow mismatch',tank_id=tid,target=coverage)
        except (KeyError,ValueError,TypeError):error('Invalid flow timestamp',tank_id=tid,target=coverage)
        check(numeric_valid(lambda:number(seg['fixed_net_flow_bbl_per_hour'])==number(seg['fixed_net_flow_bbl_per_hour'])),'Invalid flow value',tank_id=tid,target=coverage)
    for tid in tankmap:
        try:
            cursor=timestamp(flow['inventory_as_of_at']);end=timestamp(flow['flow_horizon_end_at'])
            check(cursor<end,'Invalid flow horizon',tank_id=tid,target=coverage)
            for a,b in sorted(by[tid]):
                if a!=cursor:error('Flow gap or overlap at '+cursor.isoformat(),tank_id=tid,target=coverage)
                cursor=b
            check(cursor==end,'Incomplete flow coverage',tank_id=tid,target=coverage)
        except (ValueError,TypeError,KeyError):error('Invalid flow horizon',tank_id=tid,target=coverage)
    warnings=[{'code':'OPERATIONAL_POLICY_PENDING','message':'Berths 4-1/4-2 operational rules are not validated; scheduling feasibility not evaluated.'}, {'code':'LAYCAN_SETTLEMENT_POLICY_PENDING','message':'Settlement rules for arrivals after Laycan To remain pending.'}]
    return {'scenario_id':'V5_3TANK','source_fingerprints':{a['logical_filename']:a['sha256'] for a in s['artifacts']},'schema_version':source.get('schema_version'),'vessel_count':len(calls),'berth_count':len(berths),'tank_count':len(tanks),'hard_fix_count':sum(c.get('fixed_etb_is_hard') is True for c in calls),'ordinary_call_count':sum(c.get('fixed_etb_is_hard') is False for c in calls),'cargo_group_counts':dict(Counter(c.get('cargo_group') for c in calls)),'eligible_berth_count_by_call':{c.get('call_id'):len(c.get('eligible_berth_ids',[])) for c in calls},'invalid_call_ids':sorted(invalid),'tank_connection_issues':connection,'flow_record_count':len(segments),'flow_coverage_issues':coverage,'errors':errors,'warnings':warnings,'status':'INPUT_INVALID' if errors else 'OPERATIONAL_POLICY_PENDING','data_preflight_pass':not errors,'input_status':'INPUT_INVALID' if errors else 'INPUT_VALID'}
