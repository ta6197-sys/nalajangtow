"""Build V5 inbound GRT windows from sourced daylight and explicit berth rules."""
import copy
import csv
from datetime import timedelta
import io
from pathlib import Path
from ..integrated_input.validator import number,timestamp
from ..integrated_input.provenance import digest
from ..physical_scheduling.pilotage import derive_windows


def prepare_pilotage_policy(snapshot,call_ids,eligible,policy,context,as_of_at,candidate_etbs):
    """Return a policy or unresolved issue; departure windows are separate inputs."""
    def pending(reason,**details):return None,{'code':'UNSUPPORTED_OPERATIONAL_POLICY','reason':reason,**details}
    if not isinstance(context,dict):return pending('Actual daylight data, berth night permissions and separate departure windows are required')
    if not context.get('daylight_source'):return pending('Daylight source provenance is required')
    try:
        raw=Path(context['daylight_path']).read_bytes();daylight=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        if not daylight:return pending('Daylight data is empty')
        begin=timestamp(as_of_at);end=begin+timedelta(hours=72)
        needed={(begin+timedelta(days=i)).date().isoformat() for i in range(4)}
        days=[d for d in daylight if d.get('date_kst') in needed]
        if {d.get('date_kst') for d in days}!=needed:return pending('Daily sunrise and sunset coverage missing',dates=sorted(needed-{d.get('date_kst') for d in days}))
        calls={c['call_id']:c for c in snapshot['calls']};new=copy.deepcopy(policy)
        permissions=context['night_berthing_permissions'];departures=context['departure_windows_by_berth_call']
        for cid in call_ids:
            c=calls[cid];grt=number(c['physical']['grt']);draft=number(c['physical']['draft_m'])
            if grt<30000 and draft>=number('10.5'):return pending('Inbound draft >= 10.5 m below 30,000 GRT remains unresolved',call_id=cid)
            candidates=[c['fix_etb_at']] if c.get('fixed_etb_is_hard') else candidate_etbs.get(cid,[])
            for etb_value in candidates:
                etb=timestamp(etb_value)
                if not begin<=etb<end:continue
                # The event definition and applicability of the 02:00-08:00 rule
                # are unresolved, so do not accept any potentially affected option.
                for offset_day in (-1,0):
                    day=(etb+timedelta(days=offset_day)).replace(hour=2,minute=0,second=0,microsecond=0)
                    for bid in eligible[cid]:
                        duration=policy['berths'][bid]['berthing_seconds'];pob=etb-timedelta(seconds=duration)
                        if pob<day+timedelta(hours=6) and etb>day:return pending('02:00-08:00 berthing restriction applicability/event definition unresolved',call_id=cid,berth_id=bid,etb_at=etb.isoformat())
            for bid in eligible[cid]:
                config=new['berths'][bid]
                if any(config.get(k)!=v for k,v in {'berthing_seconds':3600,'hose_connection_seconds':7200,'unberthing_seconds':3600}.items()) or any(config.get(k,0)!=0 for k in ('pilot_boarding_to_berthing_seconds','pilot_boarding_to_unberthing_seconds')):
                    return pending('User-specified 60/120/60 minute phases and zero pilot transit are required',call_id=cid,berth_id=bid)
                permitted=permissions.get(bid)
                if type(permitted) is not bool:return pending('Missing berth-specific night berthing permission',call_id=cid,berth_id=bid)
                outbound=departures.get(bid,{}).get(cid)
                if not isinstance(outbound,list) or not outbound:return pending('Separate explicit unberthing pilotage windows required',call_id=cid,berth_id=bid)
                offset=0 if grt<30000 else 5400 if grt<50000 else 7200
                inbound=derive_windows(c,days,night_permitted=permitted,last_pob_offset_seconds=offset,last_pob_basis='START')
                config.setdefault('pilotage_windows_by_call',{})[cid]={'berthing':inbound,'unberthing':copy.deepcopy(outbound)}
        new['v5_pilotage_provenance']={'daylight_sha256':digest(raw),'daylight_source':context['daylight_source'],'inbound':'GRT_AND_BERTH_PERMISSION','departure':'EXPLICIT_SEPARATE_WINDOWS','unresolved_0200_0800':'FAIL_CLOSED','unresolved_low_grt_deep_draft':'FAIL_CLOSED'}
        return new,None
    except (OSError,ValueError,TypeError,KeyError) as exc:return pending(str(exc))
