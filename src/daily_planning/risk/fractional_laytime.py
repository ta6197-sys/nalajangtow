"""Exact source-laytime extension of SECOND_PRECISION_SIMULATION_V1.

Physical event timestamps remain integer seconds. Only the accounting balance
may be rational. The established exhaustion boundary is unchanged: the next
60-second boundary anchored at the effective start.
"""
from datetime import timedelta
from fractions import Fraction
from contracts import EVENT_CODES, RULE_VERSION, day_credit, validate_contract
from .second_precision import (POLICY_VERSION, ASSUMPTION_VERSION, second_time,
    _calendar_boundary_seconds, _is_post_excluded)


def _seconds(delta):return delta.days*86400+delta.seconds

def _money(value):
    if value<0:raise ValueError('Negative monetary value')
    return (2*value.numerator+value.denominator)//(2*value.denominator)


def settle_intervals_exact_laytime(contract, clock_start, finish, intervals, *, allowed_seconds):
    """Apply the existing second policy to an exact rational allowance."""
    validate_contract(contract)
    allowed=Fraction(allowed_seconds)
    if allowed<0:raise ValueError('Negative allowed laytime')
    start,finish=second_time(clock_start),second_time(finish)
    events=sorted(((second_time(a),second_time(b),code) for a,b,code in intervals),key=lambda item:item[0])
    remaining=allowed;used=demurrage=pre_excluded=post_excluded=Fraction(0)
    onset=start if allowed==0 else None;holidays=contract.get('holidays',[])
    if not isinstance(holidays,list):raise ValueError('Contract holidays must be a list')
    holidays={str(day)[:10] for day in holidays};cursor=start
    for raw_start,raw_end,code in events:
        a,b=max(start,raw_start),min(finish,raw_end)
        if a>=b:continue
        if code not in EVENT_CODES:raise ValueError('Unknown event code: '+str(code))
        if a!=cursor:raise ValueError('Event timeline has a gap or overlap at '+str(cursor))
        at=a
        while at<b:
            end=min(b,_calendar_boundary_seconds(at,contract['day_mode']));elapsed=Fraction(_seconds(end-at))
            if remaining<=0:
                grace=min(elapsed,Fraction(max(0,_seconds(onset-at)))) if onset and at<onset else Fraction(0)
                pre_excluded+=grace;post_span=elapsed-grace
                if _is_post_excluded(code,contract['post_demurrage_exceptions']):post_excluded+=post_span
                else:demurrage+=post_span
            else:
                credit=Fraction(day_credit(at,contract['day_mode'],holidays))
                if code in ('OWNER_BREAKDOWN','TERMINAL_STOP'):credit=Fraction(0)
                elif code=='WEATHER':credit*= {'COUNT':Fraction(1),'EXCLUDE':Fraction(0),'HALF':Fraction(1,2)}[contract['weather_policy']]
                if not credit:pre_excluded+=elapsed
                else:
                    consumption=min(elapsed,remaining/credit);counted=min(remaining,consumption*credit)
                    remaining-=counted;used+=counted;pre_excluded+=consumption-counted
                    if remaining==0:
                        exact_elapsed=Fraction(_seconds(at-start))+consumption
                        ticks=-(-exact_elapsed.numerator//(exact_elapsed.denominator*60))
                        onset=start+timedelta(minutes=ticks)
                        grace=min(elapsed-consumption,max(Fraction(0),Fraction(_seconds(onset-at))-consumption))
                        pre_excluded+=grace;post_span=elapsed-consumption-grace
                        if _is_post_excluded(code,contract['post_demurrage_exceptions']):post_excluded+=post_span
                        else:demurrage+=post_span
            at=end
        cursor=b
    if finish>start and cursor!=finish:raise ValueError('Event timeline does not cover contract finish')
    if used+demurrage+pre_excluded+post_excluded!=max(Fraction(0),Fraction(_seconds(finish-start))):raise ValueError('Exact contract ledger does not reconcile')
    daily=Fraction(str(contract['daily_demurrage_usd']));cents=_money(demurrage*daily*100/86400)
    despatch=_money(remaining*daily*100/172800) if contract['despatch_policy']=='HALF_WORKING_TIME' else 0
    return {'contract_rule_version':POLICY_VERSION,'source_contract_rule_version':RULE_VERSION,'settlement_policy_version':POLICY_VERSION,
            'settlement_assumption_version':ASSUMPTION_VERSION,'laytime_precision':'EXACT_RATIONAL_SOURCE_V1','settlement_precision':'SECOND','simulation_only':True,
            'clock_start_at':start.isoformat(),'allowed_minutes':str(allowed/60),'allowed_seconds_exact':str(allowed),
            'laytime_used_seconds':str(used),'demurrage_seconds':str(demurrage),'pre_excluded_seconds':str(pre_excluded),'post_excluded_seconds':str(post_excluded),'unused_laytime_seconds':str(remaining),
            'first_demurrage_at':onset.isoformat() if demurrage>0 and onset else None,'demurrage_cents':cents,'despatch_cents':despatch,'net_owner_receivable_cents':cents-despatch}
