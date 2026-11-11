"""Exact documentary compatibility; no inferred vessel characteristics."""
from fractions import Fraction
from ..integrated_input.validator import tokens,number


def compatibility(call,berth):
    errors=[]
    def fail(field,reason):errors.append({'code':'VESSEL_BERTH_INCOMPATIBLE','call_id':call.get('call_id'),'berth_id':berth.get('berth_id'),'field':field,'reason':reason})
    if berth.get('active')!='Y':fail('active','Berth inactive')
    if call.get('cargo_group') not in tokens(berth.get('allowed_cargo_groups')):fail('cargo_group','Cargo not permitted')
    if call.get('direction') not in tokens(berth.get('allowed_operations')):fail('direction','Operation not permitted')
    p=call.get('physical') or {}
    try:
        if number(p['dwt_mt'])>number(berth['max_dwt_mt']):fail('dwt_mt','DWT exceeds limit')
        if not number(berth['min_loa_m'])<=number(p['loa_m'])<=number(berth['max_loa_m']):fail('loa_m','LOA outside range')
        if call['direction'] in tokens(berth.get('draft_check_operation')) or berth.get('draft_check_operation')=='BOTH':
            if number(p['draft_m'])>number(berth['max_draft_m']):fail('draft_m','Draft exceeds limit')
        minimum=berth.get('min_pbl_m')
        if minimum is None:minimum=(berth.get('workbook_supplement') or {}).get('min_pbl_m')
        if minimum is not None and number(p['pbl_m'])<number(minimum):fail('pbl_m','Parallel body below limit')
    except (ValueError,KeyError,TypeError):
        errors.append({'code':'MISSING_PHYSICAL_DATA','call_id':call.get('call_id'),'berth_id':berth.get('berth_id'),'reason':'Missing or invalid physical dimension'})
    return errors


def berth_duration_seconds(call,berth):
    duration=number(call['cargo_quantity_mt'])/number(berth['max_transfer_rate_mt_h'])*3600
    return -(-duration.numerator//duration.denominator)


def vessel_berth_duration_seconds(call,berth):
    """Exact MT and source rates, rounded up only to integer-second end time."""
    rates=[number(value) for value in (call.get('transfer_rate_mt_h'),berth.get('max_transfer_rate_mt_h')) if value is not None and value!='']
    if not rates or any(rate<=0 for rate in rates):raise ValueError('Missing or invalid vessel/berth handling rate')
    quantity=number(call['cargo_quantity_mt'])
    if quantity<=0:raise ValueError('Positive cargo mass required')
    duration=quantity*3600/min(rates)
    return -(-duration.numerator//duration.denominator)
