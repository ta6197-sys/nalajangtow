"""Derive synthetic pilotage windows from explicit daily sunrise/sunset inputs."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from ..integrated_input.validator import number,timestamp


def derive_windows(call,daylight,*,night_permitted,last_pob_offset_seconds,last_pob_basis):
    """last_pob_basis is START or COMPLETE; no default offset is supplied."""
    if type(night_permitted) is not bool:raise ValueError('Explicit berth night permission required')
    grt=number((call.get('physical') or {}).get('grt'))
    if grt<=0:raise ValueError('Positive GRT required')
    if type(last_pob_offset_seconds) is not int or last_pob_offset_seconds<0 or last_pob_basis not in ('START','COMPLETE'):
        raise ValueError('Explicit Last POB offset and basis required')
    windows=[];seen=set()
    for day in sorted(daylight,key=lambda d:d['date_kst']):
        if day['date_kst'] in seen:raise ValueError('Duplicate daylight date')
        seen.add(day['date_kst'])
        rise=timestamp(day['date_kst']+'T'+day['sunrise_kst']+'+09:00')
        setting=timestamp(day['date_kst']+'T'+day['sunset_kst']+'+09:00')
        if setting<=rise:raise ValueError('Invalid daylight interval')
        if grt<30000 and night_permitted:
            a=rise.replace(hour=0,minute=0,second=0);b=a+timedelta(days=1)
            windows.append({'start_at':a.isoformat(),'end_at':b.isoformat()})
        else:
            offset=0 if grt<30000 else last_pob_offset_seconds
            latest=setting-timedelta(seconds=offset)
            if latest<rise:raise ValueError('Last POB is before sunrise')
            row={'start_at':rise.isoformat(),'end_at':setting.isoformat()}
            if last_pob_basis=='START':row['latest_start_at']=latest.isoformat()
            else:row['end_at']=latest.isoformat()
            windows.append(row)
    if not windows:raise ValueError('Daily daylight inputs required')
    # A continuous low-GRT night window may cross midnight.
    merged=[]
    for row in windows:
        if merged and 'latest_start_at' not in row and 'latest_start_at' not in merged[-1] and row['start_at']==merged[-1]['end_at']:
            merged[-1]['end_at']=row['end_at']
        else:merged.append(dict(row))
    return merged
