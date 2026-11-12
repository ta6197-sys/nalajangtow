"""V5 contract identity and evidence bridge to the authoritative V3 ledger."""
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from ..integrated_input.provenance import digest
from ..integrated_input.validator import number,timestamp
from .settlement import settle_v3_call
from .optimizer import _clear_baseline_record

AUDIT=Path(__file__).resolve().parents[3]/'docs/risk_settlement_golden_audit_v3.json'


def clock_audit(snapshot):
    """Prior audit is a different workbook; V5 lacks a historical effective-start field."""
    raw=AUDIT.read_bytes();audit=json.loads(raw)
    ids={r['contract_id'] for r in audit['clock_start_divergences']};current={c['call_id'] for c in snapshot['calls']}
    return {'audit_sha256':digest(raw),'audit_source_workbook_sha256':audit['source_workbook_sha256'],
            'v5_workbook_sha256':snapshot.get('source_input',{}).get('source_workbook_sha256'),
            'historical_divergence_ids_in_v5':sorted(ids&current),'v5_effective_start_available':False,
            'status':'NOT_REVALIDATABLE_WITH_AVAILABLE_V5_FIELDS'}


def _unresolved(cid,code,reason):
    return {'status':'SETTLEMENT_UNRESOLVED','call_id':cid,'issues':[{'code':code,'call_id':cid,'reason':str(reason)}],'operational_approval':False}


def _event_matches_work(row,events,clock):
    """Every covered WORK interval must equal the candidate's physical work."""
    finish=timestamp(row['work_end_at']);work=[(max(clock,timestamp(a)),min(finish,timestamp(b))) for a,b in row['work_intervals']]
    work=[(a,b) for a,b in work if a<b]
    actual=[]
    for a,b,code in events:
        a=max(clock,timestamp(a));b=min(finish,timestamp(b))
        if a>=b:continue
        if code=='WORK':actual.append((a,b))
        elif any(max(a,x)<min(b,y) for x,y in work):return False
    def merge(rows):
        out=[]
        for a,b in sorted(rows):
            if out and a==out[-1][1]:out[-1]=(out[-1][0],b)
            else:out.append((a,b))
        return out
    return merge(actual)==merge(work)


def settle_v5_call(snapshot,row,*,as_of_at,evidence=None,pricing_policy='EXPLICIT_EVENTS_V1',clock_policy='BLOCK_UNVERIFIED_CLOCK'):
    cid=row.get('call_id')
    try:
        if snapshot.get('scenario_id') not in ('V5_3TANK','V5_BALANCED_154'):return _unresolved(cid,'INVALID_V5_INPUT','V5 scenario required')
        calls=[c for c in snapshot['calls'] if c.get('call_id')==cid];records=[c for c in snapshot['workbook_tables']['contracts']['rows'] if c.get('case_id')==cid]
        if len(calls)!=1 or len(records)!=1 or calls[0].get('vessel_id')!=records[0].get('vessel_id'):return _unresolved(cid,'CONTRACT_REFERENCE_UNRESOLVED','Call and source contract identity mismatch')
        if pricing_policy not in ('EXPLICIT_EVENTS_V1','CLEAR_WEATHER_BASELINE_V1'):return _unresolved(cid,'UNSUPPORTED_SETTLEMENT_POLICY','Unknown pricing policy')
        if clock_policy not in ('BLOCK_UNVERIFIED_CLOCK','SIMULATION_DERIVED_CONTRACT_TERMS'):return _unresolved(cid,'UNSUPPORTED_SETTLEMENT_POLICY','Unknown clock policy')
        audit=clock_audit(snapshot)
        if cid in audit['historical_divergence_ids_in_v5'] and clock_policy=='BLOCK_UNVERIFIED_CLOCK':return _unresolved(cid,'CLOCK_START_AUDIT_PENDING','Prior workbook had a divergence; V5 lacks the historical effective start needed to revalidate it')
        if clock_policy=='SIMULATION_DERIVED_CONTRACT_TERMS' and (snapshot.get('source_input',{}).get('data_origin')!='SYNTHETIC_FIXTURE' or snapshot['source_input'].get('operational_approval') is not False):return _unresolved(cid,'UNSUPPORTED_SETTLEMENT_POLICY','Derived-clock policy is simulation only')
        projected=deepcopy(snapshot);call=next(c for c in projected['calls'] if c['call_id']==cid);contract=records[0]
        allowance=number(contract['allowance_value'])
        if contract['laytime_basis']=='MT_PER_DAY':allowance=number(contract['quantity_mt'])*24/allowance
        elif contract['laytime_basis']!='FIXED_HOURS':return _unresolved(cid,'UNSUPPORTED_SETTLEMENT_POLICY','Unknown laytime basis')
        call['contract_id']=cid;call['workbook_allowed_hours_exact']=str(allowance)
        if pricing_policy=='CLEAR_WEATHER_BASELINE_V1':
            if snapshot.get('source_input',{}).get('data_origin')!='SYNTHETIC_FIXTURE' or snapshot['source_input'].get('operational_approval') is not False:return _unresolved(cid,'UNSUPPORTED_SETTLEMENT_POLICY','Clear-weather assumption is simulation only')
            for block in snapshot.get('workbook_tables',{}).get('restrictions',{}).get('rows',[]):
                if block.get('reason_code')=='WEATHER_DETERIORATION' and block.get('berth_id')==row.get('berth_id') and block.get('cargo_group') in (None,'',call.get('cargo_group')) and max(timestamp(block['start_local']),timestamp(call['nor_tender_at']))<min(timestamp(block['end_local']),timestamp(row['work_end_at'])):
                    return _unresolved(cid,'SIMULATION_CONTRADICTS_SOURCE_WEATHER','Clear-weather assumption conflicts with a source weather restriction')
            evidence=_clear_baseline_record(call,row,as_of_at=as_of_at)
        if not isinstance(evidence,dict):return _unresolved(cid,'MISSING_CANDIDATE_SETTLEMENT_EVIDENCE','Explicit candidate-specific event evidence required')
        if evidence.get('call_id')!=cid or evidence.get('work_start_at')!=row['work_start_at'] or evidence.get('finish_at')!=row['work_end_at']:return _unresolved(cid,'CANDIDATE_EVIDENCE_MISMATCH','Evidence does not identify the exact physical work interval')
        if not evidence.get('source_reference') or evidence.get('coverage_attested') is not True or timestamp(evidence['known_at'])>timestamp(as_of_at):return _unresolved(cid,'INVALID_SETTLEMENT_EVIDENCE','Source, complete coverage and known-at time required')
        events=evidence['event_intervals']
        priced=settle_v3_call(projected,cid,work_start_at=row['work_start_at'],finish_at=row['work_end_at'],event_intervals=events,despatch_policy=evidence.get('despatch_policy'),despatch_policy_reference=evidence.get('despatch_policy_reference'),settlement_policy=evidence.get('settlement_policy','MINUTE_V1'))
        if priced['status'] not in ('SETTLED_MINUTE_POLICY','SETTLED_SECOND_SIMULATION'):return {'status':'SETTLEMENT_UNRESOLVED','call_id':cid,'issues':priced['issues'],'operational_approval':False}
        from datetime import datetime
        clock=datetime.fromisoformat(priced['settlement']['clock_start_at'])
        if clock.tzinfo is None:clock=clock.replace(tzinfo=ZoneInfo('Asia/Seoul'))
        if not _event_matches_work(row,events,clock):return _unresolved(cid,'CANDIDATE_EVENT_MISMATCH','WORK evidence does not match the candidate work intervals')
        if pricing_policy=='CLEAR_WEATHER_BASELINE_V1':
            priced['settlement'].pop('despatch_cents',None);priced['settlement'].pop('net_owner_receivable_cents',None)
            priced['gross_demurrage_only']=True;priced['simulation_assumptions']=['NO_WEATHER_INTERRUPTION','NO_OWNER_FAULT','NO_TERMINAL_FAULT','NO_OTHER_INTERRUPTION']
        return {'status':'SETTLED','call_id':cid,'demurrage_cents':priced['settlement']['demurrage_cents'],'settlement':priced,'pricing_policy':pricing_policy,'clock_policy':clock_policy,'clock_audit':audit,'simulation_only':snapshot.get('source_input',{}).get('data_origin')=='SYNTHETIC_FIXTURE' or pricing_policy=='CLEAR_WEATHER_BASELINE_V1' or clock_policy=='SIMULATION_DERIVED_CONTRACT_TERMS' or evidence.get('settlement_policy')=='SECOND_PRECISION_SIMULATION_V1','evidence_source':evidence['source_reference'],'operational_approval':False}
    except (OSError,KeyError,ValueError,TypeError,ZeroDivisionError,OverflowError) as exc:return _unresolved(cid,'INVALID_SETTLEMENT_INPUT',exc)


def price_v5_schedule(snapshot,rows,*,as_of_at,settlement_records,pricing_policy='EXPLICIT_EVENTS_V1',clock_policy='BLOCK_UNVERIFIED_CLOCK'):
    result={};issues=[];total=0
    ids=[row.get('call_id') for row in rows]
    if not ids or None in ids or len(ids)!=len(set(ids)):
        return {'status':'SETTLEMENT_UNRESOLVED','total_demurrage_cents':None,'vessel_demurrage_breakdown':{},'issues':[{'code':'INVALID_CANDIDATE_CALLS','reason':'Unique nonempty call IDs required'}],'operational_approval':False}
    for row in rows:
        records=[r for r in settlement_records if r.get('call_id')==row['call_id'] and r.get('work_start_at')==row['work_start_at'] and r.get('finish_at')==row['work_end_at']]
        if pricing_policy=='EXPLICIT_EVENTS_V1' and len(records)!=1:priced=_unresolved(row['call_id'],'MISSING_CANDIDATE_SETTLEMENT_EVIDENCE','Exactly one candidate-specific event record required')
        else:priced=settle_v5_call(snapshot,row,as_of_at=as_of_at,evidence=records[0] if records else None,pricing_policy=pricing_policy,clock_policy=clock_policy)
        result[row['call_id']]=priced
        if priced['status']!='SETTLED':issues.extend(priced['issues'])
        else:total+=priced['demurrage_cents']
    return {'status':'SETTLED' if not issues else 'SETTLEMENT_UNRESOLVED','total_demurrage_cents':total if not issues else None,'vessel_demurrage_breakdown':result,'issues':issues,'operational_approval':False}
