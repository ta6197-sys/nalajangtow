"""Fail-closed V3 adapter to the existing minute-boundary contract evaluator."""
from fractions import Fraction
from contracts import contract_clock_start, minute_time, settle_intervals, validate_contract, RULE_VERSION
from ..integrated_input.validator import number, timestamp
from ..integrated_input.provenance import canonical, digest

VERSION='V3_CONTRACT_ADAPTER_V1'


def settle_v3_call(snapshot, call_id, *, work_start_at, finish_at, event_intervals,
                   despatch_policy=None, despatch_policy_reference=None, settlement_policy='MINUTE_V1'):
    """Settle a supplied, fully covered contractual event timeline.

    Event intervals are explicit (start, end, EVENT_CODE) tuples covering the
    contract clock through finish. No weather, laycan or exception is inferred.
    """
    def unresolved(code,reason):
        return {'model_version':VERSION,'status':'UNRESOLVED','issues':[{'code':code,'call_id':call_id,'reason':str(reason)}], 'operational_approval':False}
    try:
        matches=[c for c in snapshot['calls'] if c['call_id']==call_id]
        records=[r for r in snapshot['workbook_tables']['contracts']['rows'] if r['case_id']==call_id]
        if len(matches)!=1 or len(records)!=1:return unresolved('CONTRACT_REFERENCE_UNRESOLVED','Expected exactly one call and workbook contract')
        call,source=matches[0],records[0]
        if call.get('contract_id')!=source['case_id'] or call.get('vessel_id')!=source.get('vessel_id'):
            return unresolved('CONTRACT_REFERENCE_UNRESOLVED','Call, contract and vessel identities do not match')
        if snapshot.get('source_input',{}).get('planning_policy',{}).get('laycan_exception')!='SCENARIOS_ONLY_NOT_ACTIVE_IN_SETTLEMENT':
            return unresolved('UNSUPPORTED_SETTLEMENT_POLICY','Verified V3 laycan policy required')
        if settlement_policy not in ('MINUTE_V1','SECOND_PRECISION_SIMULATION_V1'):
            return unresolved('UNSUPPORTED_SETTLEMENT_POLICY','Unknown explicit settlement version')
        if settlement_policy=='SECOND_PRECISION_SIMULATION_V1' and (snapshot.get('source_input',{}).get('data_origin')!='SYNTHETIC_FIXTURE' or snapshot.get('source_input',{}).get('operational_approval') is not False):
            return unresolved('UNSUPPORTED_SETTLEMENT_POLICY','Second-precision policy is approved only for unapproved synthetic data')
        if despatch_policy not in ('NONE','HALF_WORKING_TIME') or not despatch_policy_reference:
            return unresolved('MISSING_SETTLEMENT_POLICY','Explicit despatch policy and its source reference required')
        supplied=number(call['allowed_hours']);source_exact=number(call['workbook_allowed_hours_exact'])
        if number(source['quantity_mt'])!=number(call['cargo_quantity_mt']):return unresolved('CONTRACT_TERMS_MISMATCH','Cargo mass')
        if source['laytime_basis']=='MT_PER_DAY':
            if number(source['allowance_value'])<=0:raise ValueError('Invalid contract allowance')
            calculated=number(source['quantity_mt'])*24/number(source['allowance_value'])
        elif source['laytime_basis']=='FIXED_HOURS':calculated=number(source['allowance_value'])
        else:return unresolved('UNSUPPORTED_SETTLEMENT_POLICY','Unknown source laytime basis')
        if calculated!=source_exact:return unresolved('CONTRACT_TERMS_MISMATCH','Workbook laytime formula')
        differs=supplied!=source_exact
        if differs:
            verified_export=supplied==Fraction(round(source_exact*1000000),1000000)
            if settlement_policy=='MINUTE_V1' or not verified_export:
                return unresolved('UNRESOLVED_SETTLEMENT_PRECISION','JSON hours differ from the exact workbook formula')
        allowed=(source_exact if settlement_policy=='SECOND_PRECISION_SIMULATION_V1' else supplied)*60
        allowed_seconds=allowed*60
        if allowed.denominator!=1 and settlement_policy!='SECOND_PRECISION_SIMULATION_V1':
            return unresolved('UNSUPPORTED_SUBMINUTE_LAYTIME','Minute evaluator requires integral allowed minutes')
        # The legacy validator requires integral minutes. For the optional exact
        # seconds extension the clock rules ignore this placeholder; the real
        # allowed quantity is passed explicitly to the second-precision ledger.
        ledger_seconds=allowed_seconds.numerator if allowed.denominator!=1 and allowed_seconds.denominator==1 else None
        fractional_laytime=allowed_seconds.denominator!=1
        for key,source_key in [('nor_valid','nor_valid'),('notice_hours','notice_hours'),('day_mode','day_mode'),('weather_policy','weather_policy'),('post_demurrage_exceptions','post_demurrage_exceptions'),('early_start_if_work','early_start_if_work'),('daily_demurrage_usd','demurrage_usd_day')]:
            left,right=call[key],source[source_key]
            if key in ('notice_hours','daily_demurrage_usd'):
                if number(left)!=number(right):return unresolved('CONTRACT_TERMS_MISMATCH',key)
            elif left!=right:return unresolved('CONTRACT_TERMS_MISMATCH',key)
        if timestamp(call['nor_tender_at'])!=timestamp(source['nor_tender_local']):return unresolved('CONTRACT_TERMS_MISMATCH','NOR timestamp')
        contract={'nor_at':call['nor_tender_at'],'nor_valid':call['nor_valid'],'notice_hours':call['notice_hours'],
                  'allowed_minutes':str(allowed.numerator if allowed.denominator==1 else 0),'daily_demurrage_usd':call['daily_demurrage_usd'],
                  'day_mode':call['day_mode'],'weather_policy':call['weather_policy'],
                  'post_demurrage_exceptions':call['post_demurrage_exceptions'],
                  'early_start_if_work':call['early_start_if_work'],'despatch_policy':despatch_policy,
                  'holidays':call['contract_holidays']}
        validate_contract(contract)
        # Never round second-level events to a minute. Their settlement is unresolved.
        work=timestamp(work_start_at);finish=timestamp(finish_at)
        if settlement_policy=='MINUTE_V1':minute_time(work);minute_time(finish)
        if finish<work:return unresolved('INVALID_SETTLEMENT_TIME','Finish precedes work start')
        if settlement_policy=='SECOND_PRECISION_SIMULATION_V1':
            from ..risk.second_precision import contract_clock_start_seconds,settle_intervals_seconds
            clock=contract_clock_start_seconds(contract,work)
        else:clock=contract_clock_start(contract,work)
        intervals=[]
        for row in event_intervals:
            if not isinstance(row,(list,tuple)) or len(row)!=3:raise ValueError('Expected explicit (start, end, code) intervals')
            a,b=timestamp(row[0]),timestamp(row[1])
            if settlement_policy=='MINUTE_V1':minute_time(a);minute_time(b)
            if b<=a:raise ValueError('Invalid event interval')
            intervals.append((a,b,row[2]))
        if settlement_policy=='SECOND_PRECISION_SIMULATION_V1' and fractional_laytime:
            from ..risk.fractional_laytime import settle_intervals_exact_laytime
            settlement=settle_intervals_exact_laytime(contract,clock,finish,intervals,allowed_seconds=allowed_seconds)
        elif settlement_policy=='SECOND_PRECISION_SIMULATION_V1':settlement=settle_intervals_seconds(contract,clock,finish,intervals,allowed_seconds=ledger_seconds)
        else:settlement=settle_intervals(contract,clock,finish,intervals)
        return {'model_version':VERSION,'status':('SETTLED_SECOND_SIMULATION' if settlement_policy=='SECOND_PRECISION_SIMULATION_V1' else 'SETTLED_MINUTE_POLICY'),'call_id':call_id,'contract_rule_version':RULE_VERSION,
                'source_fingerprint':digest(canonical(snapshot).encode()),
                'input_fingerprint':digest(canonical({'call_id':call_id,'work_start_at':work_start_at,'finish_at':finish_at,'event_intervals':event_intervals,'despatch_policy':despatch_policy,'despatch_policy_reference':despatch_policy_reference,**({'settlement_policy':settlement_policy} if settlement_policy!='MINUTE_V1' else {})}).encode()),
                'despatch_policy_reference':despatch_policy_reference,'settlement_policy':settlement_policy,'source_laytime_hours_exact':str(source_exact),'json_laytime_hours':str(supplied),'verified_json_rounding_difference':differs,'settlement':settlement,
                'pending_checks':['PHYSICAL_SCHEDULE','EVENT_COVERAGE_SOURCE_AND_WEATHER','OPERATOR_APPROVAL'], 'operational_approval':False}
    except (ValueError,KeyError,TypeError,ZeroDivisionError,OverflowError) as exc:
        code='UNSUPPORTED_SUBMINUTE_SETTLEMENT' if 'whole minutes' in str(exc) else 'INVALID_SETTLEMENT_INPUT'
        return unresolved(code,exc)
