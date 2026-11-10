"""Strict data validation only; never physical scheduling approval."""
from collections import defaultdict
from datetime import date,datetime,timedelta
from fractions import Fraction
import re
from zoneinfo import ZoneInfo

PROFILE={'calls':140,'berths':11,'tanks':30,'fix':15,'segments':6960,'capacity':10000000,'flow_start':'2026-11-03T00:00+09:00','flow_end':'2026-12-31T00:00+09:00'}
KST=ZoneInfo('Asia/Seoul')


def timestamp(value):
    if not isinstance(value,str): raise ValueError('ISO timestamp required')
    at=datetime.fromisoformat(value)
    if at.tzinfo is None or at.utcoffset()!=timedelta(hours=9) or at.microsecond:
        raise ValueError('Integer-second Asia/Seoul timestamp (+09:00) required')
    return at.astimezone(KST)


def number(value):
    if isinstance(value,(bool,float)) or value is None: raise ValueError('Exact decimal/integer required')
    return Fraction(str(value))


def tokens(value):
    if isinstance(value,list): return value
    return [v.strip() for v in str(value or '').replace(';',',').split(',') if v.strip()]


def validate(snapshot,profile=None):
    expected=PROFILE if profile is None else profile;issues=[];pending=[];contract_pending=[]
    def issue(code,field,reason,**ids): issues.append(dict(code=code,source_field=field,explanation=str(reason),**ids))
    def check(fn,code,field,**ids):
        try: fn()
        except (ValueError,TypeError,KeyError,ZeroDivisionError) as exc: issue(code,field,exc,**ids)
    def require(condition,message):
        if not condition: raise ValueError(message)
    source=snapshot['source_input'];flow=snapshot['fixed_process_flows'];tables=snapshot['workbook_tables']
    check(lambda:require(source.get('schema_version')=='DOCKTIMIZE_COORDINATOR_SYNTHETIC_INPUT_V3' and flow.get('schema_version')=='FIXED_PROCESS_FLOW_TS_V1','Unexpected source schema'),'INVALID_SCHEMA','schema_version')
    for obj in (source,flow):
        check(lambda obj=obj:require(obj.get('data_origin')=='SYNTHETIC_FIXTURE' and obj.get('operational_approval') is False and obj.get('timezone')=='Asia/Seoul' and type(obj.get('seed')) is int,'Invalid source origin/approval/timezone'),'INVALID_SCHEMA','source_metadata')
    check(lambda:require(source.get('seed')==flow.get('seed') and source.get('process_flow_schema_version')==flow.get('schema_version'),'Flow source metadata mismatch'),'INVALID_SCHEMA','process_flow_source')
    if 'flow_start' in expected:
        check(lambda:require(timestamp(flow['inventory_as_of_at'])==timestamp(expected['flow_start']) and timestamp(flow['flow_horizon_end_at'])==timestamp(expected['flow_end']),'Flow period differs from expected profile'),'INVALID_TIME','flow_horizon')
    check(lambda:require(isinstance(source.get('source_workbook_sha256'),str) and re.fullmatch('[0-9a-fA-F]{64}',source['source_workbook_sha256']) is not None,'Invalid historical parent-source hash'),'INVALID_SCHEMA','source_workbook_sha256')
    policy=source.get('planning_policy',{})
    check(lambda:require(policy.get('laycan_exception')=='SCENARIOS_ONLY_NOT_ACTIVE_IN_SETTLEMENT' and policy.get('free_call_etb')=='SOLVER_OUTPUT' and policy.get('fix_berth')=='NOT_FIXED','Unsupported settlement or assignment policy'),'INVALID_SCHEMA','planning_policy')
    indices={}
    for label,rows,key in [('calls',snapshot['calls'],'call_id'),('berths',snapshot['berths'],'berth_id'),('tanks',snapshot['tanks'],'tank_id')]:
        ids=[r.get(key) for r in rows];indices[label]={r.get(key):r for r in rows}
        check(lambda ids=ids:require(all(isinstance(i,str) and i and i.strip()==i for i in ids) and len(ids)==len(set(ids)),'Missing or duplicate identifier'),'INVALID_SCHEMA',key)
        count=len([r for r in rows if r.get('active')=='Y']) if label=='berths' else len(rows)
        check(lambda count=count,label=label:require(count==expected[label],f'Expected {expected[label]}, got {count}'),'INVALID_SCHEMA',label+'.count')
    vessels=[r.get('vessel_id') for r in snapshot['calls']]
    check(lambda:require(all(isinstance(v,str) and v and v.strip()==v for v in vessels) and len(vessels)==len(set(vessels)),'Missing or duplicate vessel identifier'),'INVALID_SCHEMA','vessel_id')
    groups={r.get('rule_id'):r for r in tables['groups']['rows'] if r.get('rule_id')}
    contracts={r.get('case_id'):r for r in tables['contracts']['rows']}
    eta_rows={r.get('case_id'):r for r in tables['eta']['rows']}
    for key,field in [('contracts','case_id'),('profiles','case_id'),('eta','case_id'),('calls','call_id')]:
        values=[r.get(field) for r in tables[key]['rows']]
        check(lambda values=values:require(None not in values and len(values)==len(set(values)),'Missing/duplicate workbook identity'),'INVALID_SCHEMA',tables[key]['sheet'])
    for call in snapshot['calls']:
        cid=call.get('call_id');ids={'call_id':cid};p=call.get('physical')
        check(lambda:require(call.get('contract_id') is not None,'Unresolved contract relationship'),'UNKNOWN_REFERENCE','contract_id',**ids)
        check(lambda:require(p is not None and p.get('vessel_id')==call.get('vessel_id') and p.get('cargo_group')==call.get('cargo_group') and all(number(p.get(k))>0 for k in ('dwt_mt','loa_m','draft_m','grt')),'Mandatory vessel profile missing or inconsistent'),'MISSING_PHYSICAL_DATA','17_선박제원',**ids)
        for k in ('eta_at','nor_tender_at','laycan_from_at','laycan_to_at'):check(lambda k=k:timestamp(call.get(k)),'INVALID_TIME',k,**ids)
        check(lambda:require(timestamp(call['laycan_from_at'])<=timestamp(call['laycan_to_at']),'Reversed Laycan'),'INVALID_TIME','laycan',**ids)
        check(lambda:require(all(number(call.get(k))>0 for k in ('cargo_quantity_mt','cargo_volume_bbl','allowed_hours','daily_demurrage_usd','work_duration_reference_minutes','min_work_duration_reference_berth_minutes')) and number(call.get('notice_hours'))>=0,'Nonpositive quantity or duration'),'INVALID_QUANTITY','cargo_contract_duration',**ids)
        check(lambda:require('density_kg_per_bbl_exact' in call and number(call['cargo_quantity_kg_exact'])==number(call['cargo_volume_bbl'])*number(call['density_kg_per_bbl_exact']),'Unverified mass/volume conversion'),'INVALID_QUANTITY','density',**ids)
        contract=contracts.get(cid,{})
        def contract_match():
            require(number(contract['quantity_mt'])==number(call['cargo_quantity_mt']),'Contract mass mismatch')
            allowed=number(contract['allowance_value'])
            if contract['laytime_basis']=='MT_PER_DAY': allowed=number(contract['quantity_mt'])*24/allowed
            else: require(contract['laytime_basis']=='FIXED_HOURS','Unknown laytime basis')
            supplied=number(call['allowed_hours'])
            if allowed!=supplied:
                require(supplied==Fraction(round(allowed*1000000),1000000),'Contract allowed laytime mismatch beyond exact six-decimal export')
                contract_pending.append({'code':'UNRESOLVED_SETTLEMENT_PRECISION','call_id':cid,'source_field':'allowed_hours','json_hours':call['allowed_hours'],'workbook_formula_hours_exact':str(allowed),'explanation':'Exact six-decimal export verified; neither source term is overwritten; settlement blocked pending explicit precision policy.'})
            require(timestamp(contract['nor_tender_local'])==timestamp(call['nor_tender_at']),'Contract NOR mismatch')
            for ck,jk in [('notice_hours','notice_hours'),('demurrage_usd_day','daily_demurrage_usd')]:
                require(number(contract[ck])==number(call[jk]),'Contract numeric term mismatch: '+ck)
            for field in ('day_mode','weather_policy','post_demurrage_exceptions','nor_valid','early_start_if_work'):
                require(contract[field]==call[field],'Contract term mismatch: '+field)
        check(contract_match,'INVALID_SCHEMA','03_계약조건',**ids)
        eta=eta_rows.get(cid,{})
        check(lambda:require(eta.get('vessel_id')==call.get('vessel_id') and timestamp(eta['eta_local'])==timestamp(call['eta_at']),'Workbook ETA identity mismatch'),'UNKNOWN_REFERENCE','12_ETA_ETB',**ids)
        hard=call.get('fixed_etb_is_hard');fix=call.get('fix_etb_at')
        check(lambda:require(type(hard) is bool and ((hard and bool(fix)) or (not hard and fix is None)),'FIX flag and timestamp disagree'),'FIX_PRECHECK_FAILED','fix_etb_at',**ids)
        if fix is not None:check(lambda:timestamp(fix),'INVALID_TIME','fix_etb_at',**ids)
        if hard and fix is not None:check(lambda:require(timestamp(fix)>=timestamp(call['eta_at']),'FIX ETB precedes ETA'),'FIX_PRECHECK_FAILED','fix_etb_at',**ids)
        check(lambda:require((eta.get('fix_etb_flag')=='Y')==hard and (timestamp(eta['fixed_etb_local'])==timestamp(fix) if hard else eta.get('fixed_etb_local') is None),'Workbook FIX differs'),'FIX_PRECHECK_FAILED','12_ETA_ETB.fix',**ids)
        for k in ('etb_at','etu_at','fixed_berth_id'):
            if call.get(k) is not None:issue('FIX_PRECHECK_FAILED',k,'Unexpected immutable output assignment',**ids)
        ref=call.get('baseline_reference_only',{})
        for field,index in [('berth_id','berths'),('tank_id','tanks')]:
            if ref.get(field) is not None and ref[field] not in indices[index]:issue('UNKNOWN_REFERENCE','baseline_reference_only.'+field,ref[field],**ids)
        check(lambda:require(isinstance(call.get('contract_holidays'),list) and all(date.fromisoformat(d) for d in call['contract_holidays']),'Invalid holidays'),'INVALID_TIME','contract_holidays',**ids)
        check(lambda:require(call.get('direction') in ('LOAD','DISCHARGE') and bool(call.get('cargo_group')),'Missing cargo direction/group'),'INVALID_SCHEMA','cargo_direction',**ids)
        check(lambda:require(call.get('day_mode') in ('SHINC','SHEX_SUN','SHEX_WEEKEND','SHEX_SAT12') and call.get('weather_policy') in ('COUNT','EXCLUDE','HALF') and call.get('post_demurrage_exceptions') in ('NONE','OWNER_ONLY','OWNER_AND_TERMINAL') and call.get('nor_valid') in ('Y','N') and call.get('early_start_if_work') in ('Y','N'),'Unsupported contract terms'),'INVALID_SCHEMA','contract_terms',**ids)
    check(lambda:require(sum(c.get('fixed_etb_is_hard') is True for c in snapshot['calls'])==expected['fix'],'Unexpected FIX count'),'FIX_PRECHECK_FAILED','fix.count')
    for berth in snapshot['berths']:
        bid=berth.get('berth_id');ids={'berth_id':bid}
        check(lambda:require(all(number(berth.get(k))>0 for k in ('max_dwt_mt','max_loa_m','max_draft_m','max_transfer_rate_mt_h')) and 0<=number(berth.get('min_loa_m'))<=number(berth['max_loa_m']),'Invalid berth dimensions/rates'),'INVALID_QUANTITY','berth_dimensions',**ids)
        check(lambda:require(bool(tokens(berth.get('allowed_cargo_groups'))) and set(tokens(berth.get('allowed_operations')))<= {'LOAD','DISCHARGE'} and bool(tokens(berth.get('allowed_operations'))),'Missing cargo or direction compatibility'),'MISSING_PHYSICAL_DATA','berth_compatibility',**ids)
        for field in ('cargo_group_conflict_id','docking_conflict_id'):
            if berth.get(field) and berth[field] not in groups:issue('UNKNOWN_REFERENCE',field,berth[field],**ids)
        for tid in tokens(berth.get('connected_tank_ids')):
            if tid not in indices['tanks']:issue('UNKNOWN_REFERENCE','connected_tank_ids',tid,**ids)
            elif bid not in tokens(indices['tanks'][tid].get('connected_berth_ids')):issue('UNKNOWN_REFERENCE','bidirectional_connection',tid,**ids)
            elif indices['tanks'][tid].get('cargo_group') not in tokens(berth.get('allowed_cargo_groups')):issue('MISSING_PHYSICAL_DATA','connection.cargo_group',tid,**ids)
    for gid,group in groups.items():
        members=tokens(group.get('berth_ids'))
        if not members or any(b not in indices['berths'] for b in members):issue('UNKNOWN_REFERENCE','group.berth_ids',gid)
        elif any(gid not in (indices['berths'][b].get('cargo_group_conflict_id'),indices['berths'][b].get('docking_conflict_id')) for b in members):issue('UNKNOWN_REFERENCE','group.reverse_reference',gid)
    for tank in snapshot['tanks']:
        tid=tank.get('tank_id');ids={'tank_id':tid}
        check(lambda:require(0<=number(tank.get('min_inventory_bbl'))<=number(tank.get('initial_inventory_bbl'))<=number(tank.get('capacity_bbl')) and number(tank['capacity_bbl'])>0 and number(tank.get('max_receipt_rate_bbl_per_hour'))>0 and number(tank.get('max_dispatch_rate_bbl_per_hour'))>0 and bool(tank.get('cargo_group')),'Invalid tank inventory/capacity/rates'),'INVALID_QUANTITY','tank_limits',**ids)
        for bid in tokens(tank.get('connected_berth_ids')):
            if bid not in indices['berths'] or tid not in tokens(indices['berths'][bid].get('connected_tank_ids')):issue('UNKNOWN_REFERENCE','connected_berth_ids',bid,**ids)
    check(lambda:require(sum((number(t['capacity_bbl']) for t in snapshot['tanks']),Fraction(0))==expected['capacity'],'Installed capacity differs from profile'),'INVALID_QUANTITY','total_capacity_bbl')
    by_tank=defaultdict(list)
    segments=flow.get('segments',[])
    check(lambda:require(len(segments)==expected['segments'] and flow.get('record_count')==len(segments) and flow.get('tank_count')==len(snapshot['tanks']) and flow.get('interval_hours')==6,'Flow profile mismatch'),'INVALID_SCHEMA','flow.count')
    for seg in segments:
        tid=seg.get('tank_id')
        if tid not in indices['tanks']:issue('UNKNOWN_REFERENCE','flow.tank_id',tid,tank_id=tid)
        try:
            a=timestamp(seg.get('start_at'));b=timestamp(seg.get('end_at'))
            if b-a!=timedelta(hours=6):raise ValueError('Expected positive six-hour segment')
            by_tank[tid].append((a,b))
        except (ValueError,TypeError):issue('INVALID_TIME','flow.segment','Invalid timestamp or duration',tank_id=tid)
        check(lambda seg=seg:number(seg.get('fixed_net_flow_bbl_per_hour')),'INVALID_QUANTITY','fixed_net_flow_bbl_per_hour',tank_id=tid)
    for tid in indices['tanks']:
        def coverage():
            cursor=timestamp(flow['inventory_as_of_at']);end=timestamp(flow['flow_horizon_end_at'])
            require(cursor<end,'Invalid flow horizon')
            for a,b in sorted(by_tank[tid]):
                require(a==cursor,'Gap or overlap at '+cursor.isoformat());cursor=b
            require(cursor==end,'Missing final coverage')
        check(coverage,'FLOW_GAP','flow.coverage',tank_id=tid)
    for table,key,rows,fields in [
        ('tanks','tank_id',snapshot['tanks'],{'capacity_bbl':'capacity_bbl','initial_inventory_bbl':'initial_inventory_bbl','min_inventory_bbl':'min_inventory_bbl','max_receipt_rate_bbl_per_hour':'max_receipt_rate_bbl_h','max_dispatch_rate_bbl_per_hour':'max_dispatch_rate_bbl_h'}),
        ('berths','berth_id',snapshot['berths'],{'max_dwt_mt':'max_dwt_mt','max_loa_m':'max_loa_m','min_loa_m':'min_loa_m','max_draft_m':'max_draft_m','max_transfer_rate_mt_h':'max_transfer_rate_mt_h','switching_min':'switching_min','hose_disconnect_min':'hose_disconnect_min'})]:
        workbook={r.get(key):r for r in tables[table]['rows']}
        for row in rows:
            check(lambda row=row:require(row.get(key) in workbook and all(number(row.get(a))==number(workbook[row[key]].get(b)) for a,b in fields.items()),'Workbook and JSON physical fields differ'),'INVALID_QUANTITY',table+'.workbook_match',**{key:row.get(key)})
    for row in tables['sof']['rows']:
        if row.get('case_id') not in contracts:issue('UNKNOWN_REFERENCE','SOF.case_id',row.get('case_id'))
        check(lambda row=row:require(timestamp(row['event_start_local'])<timestamp(row['event_end_local']),'Invalid SOF interval'),'INVALID_TIME','SOF.interval')
    for r in tables['restrictions']['rows']:
        if r.get('berth_id') not in indices['berths']:issue('UNKNOWN_REFERENCE','restriction.berth_id',r.get('berth_id'))
        check(lambda r=r:require(timestamp(r['start_local'])<timestamp(r['end_local']),'Invalid restriction interval'),'INVALID_TIME','restriction.time')
    pending.append({'code':'UNSUPPORTED_OPERATIONAL_POLICY','source_field':'operational_rules.dock_4142','explanation':'Raw policy retained; phase/resource scheduling implementation is pending.'})
    return {'status':'VALID' if not issues else 'INVALID_SCHEMA','data_preflight_pass':not issues,'issues':issues,'pending_operational_validation':pending,'pending_contract_validation':contract_pending,'settlement_ready':not contract_pending and not issues,'physical_scheduling_feasibility':'NOT_EVALUATED','operational_approval':False,'counts':{'calls':len(snapshot['calls']),'berths':len(snapshot['berths']),'tanks':len(snapshot['tanks']),'fix':sum(c.get('fixed_etb_is_hard') is True for c in snapshot['calls']),'segments':len(segments)}}
