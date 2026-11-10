"""Source-traceable synthetic flow extension, separate from original inputs."""
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
from .validator import PROFILE,number,timestamp,validate
from .provenance import canonical,digest

VERSION='V3_SYNTHETIC_FLOW_EXTENSION_V1'


def build_extended_snapshot(snapshot, *, base_flow_path, extension_path):
    """Validate and combine source files without changing either input."""
    base_raw=Path(base_flow_path).read_bytes();ext_raw=Path(extension_path).read_bytes()
    base=json.loads(base_raw,parse_float=str);ext=json.loads(ext_raw,parse_float=str)
    if snapshot.get('source_flow_input')!=base:raise ValueError('Base flow file differs from preserved source flow input')
    normalized_base=deepcopy(base)
    for segment in normalized_base['segments']:
        for key in ('start_at','end_at'):segment[key]=timestamp(segment[key]).isoformat()
    if snapshot['fixed_process_flows']!=normalized_base:raise ValueError('Normalized base flow differs from source')
    if not validate(snapshot)['data_preflight_pass']:raise ValueError('Base snapshot preflight failed')
    base_sha=sha256(base_raw).hexdigest()
    if ext.get('schema_version')!='DOCKTIMIZE_FLOW_EXTENSION_INPUT_V1' or ext.get('data_origin')!='SYNTHETIC_FIXTURE' or ext.get('operational_approval') is not False:raise ValueError('Invalid synthetic extension metadata')
    if ext.get('base_flow_sha256')!=base_sha or base_sha not in ext.get('source_reference',''):raise ValueError('Base flow SHA-256 mismatch')
    if ext.get('timezone')!='Asia/Seoul' or ext.get('interval_hours')!=6 or not ext.get('source_reference'):raise ValueError('Invalid extension units, interval or provenance')
    start,end=timestamp(ext['extension_start_at']),timestamp(ext['extension_end_at']);known=timestamp(ext['known_at'])
    if start!=timestamp(base['flow_horizon_end_at']) or end<=start:raise ValueError('Extension must start at base horizon end')
    tanks={x['tank_id'] for x in snapshot['tanks']};segments=ext['segments']
    if ext.get('tank_count')!=len(tanks) or ext.get('record_count')!=len(segments):raise ValueError('Extension count metadata mismatch')
    base_index={(x['tank_id'],timestamp(x['start_at'])):x for x in base['segments']};by_tank=defaultdict(list)
    for row in segments:
        tid=row['tank_id'];a=timestamp(row['start_at']);b=timestamp(row['end_at']);rate=number(row['fixed_net_flow_bbl_per_hour'])
        if tid not in tanks or b-a!=timedelta(hours=6) or timestamp(row['known_at'])!=known or row['source_reference']!=ext['source_reference']:raise ValueError('Invalid extension segment '+str(tid))
        source=base_index.get((tid,a-timedelta(days=14)))
        if ext.get('generation_method')!='EXACT_14_DAY_REPETITION_PER_TANK_FROM_EXISTING_SYNTHETIC_FLOW' or source is None or number(source['fixed_net_flow_bbl_per_hour'])!=rate:raise ValueError('14-day source pattern mismatch '+tid+' '+a.isoformat())
        by_tank[tid].append((a,b))
    for tid in tanks:
        cursor=start
        for a,b in sorted(by_tank[tid]):
            if a!=cursor:raise ValueError('Flow gap or overlap '+tid+' '+cursor.isoformat())
            cursor=b
        if cursor!=end:raise ValueError('Incomplete extension coverage '+tid)
    result=deepcopy(snapshot);flow=result['fixed_process_flows'];flow['segments']+=deepcopy(segments);flow['record_count']=len(flow['segments']);flow['flow_horizon_end_at']=end.isoformat()
    flow['extension_provenance']={'version':VERSION,'base_flow_sha256':base_sha,'base_flow_canonical_sha256':digest(canonical(normalized_base).encode()),'extension_sha256':sha256(ext_raw).hexdigest(),'extension_input':deepcopy(ext),'extension_input_canonical_sha256':digest(canonical(ext).encode()),'extension_start_at':start.isoformat(),'base_end_original':normalized_base['flow_horizon_end_at'],'known_at':known.isoformat(),'base_segment_count':len(base['segments'])}
    return result


def validate_extended_snapshot(snapshot):
    """Revalidate base, extension provenance and extended flow profile."""
    flow=snapshot['fixed_process_flows'];meta=flow['extension_provenance'];n=meta['base_segment_count']
    if meta.get('version')!=VERSION or n!=PROFILE['segments']:raise ValueError('Unsupported extension provenance')
    base=deepcopy(flow);base.pop('extension_provenance');base['segments']=base['segments'][:n];base['record_count']=n;base['flow_horizon_end_at']=meta['base_end_original']
    if digest(canonical(base).encode())!=meta['base_flow_canonical_sha256']:raise ValueError('Base flow content changed')
    preserved=deepcopy(snapshot['source_flow_input'])
    for segment in preserved['segments']:
        for key in ('start_at','end_at'):segment[key]=timestamp(segment[key]).isoformat()
    if base!=preserved:raise ValueError('Base flow differs from preserved source')
    ext=meta['extension_input'];extra=flow['segments'][n:]
    if digest(canonical(ext).encode())!=meta['extension_input_canonical_sha256'] or extra!=ext.get('segments') or timestamp(meta['known_at'])!=timestamp(ext['known_at']):raise ValueError('Extension content or known_at changed')
    # Recheck against source metadata and the full original normalized profile.
    original=dict(snapshot,fixed_process_flows=base)
    if not validate(original)['data_preflight_pass']:raise ValueError('Base snapshot preflight failed')
    if ext.get('base_flow_sha256')!=meta.get('base_flow_sha256') or ext.get('data_origin')!='SYNTHETIC_FIXTURE' or ext.get('operational_approval') is not False:raise ValueError('Extension provenance mismatch')
    start=timestamp(meta['extension_start_at']);end=timestamp(ext['extension_end_at']);known=timestamp(ext['known_at']);base_index={(r['tank_id'],timestamp(r['start_at'])):r for r in base['segments']};by_tank=defaultdict(list)
    if ext.get('schema_version')!='DOCKTIMIZE_FLOW_EXTENSION_INPUT_V1' or ext.get('generation_method')!='EXACT_14_DAY_REPETITION_PER_TANK_FROM_EXISTING_SYNTHETIC_FLOW' or ext.get('timezone')!='Asia/Seoul' or ext.get('interval_hours')!=6 or not ext.get('source_reference') or ext.get('record_count')!=len(extra) or ext.get('tank_count')!=len(snapshot['tanks']) or timestamp(ext['extension_start_at'])!=start:raise ValueError('Invalid extension metadata')
    for row in extra:
        tid=row['tank_id'];a=timestamp(row['start_at']);b=timestamp(row['end_at']);original=base_index.get((tid,a-timedelta(days=14)))
        if b-a!=timedelta(hours=6) or timestamp(row['known_at'])!=known or row['source_reference']!=ext['source_reference'] or original is None or number(original['fixed_net_flow_bbl_per_hour'])!=number(row['fixed_net_flow_bbl_per_hour']):raise ValueError('Invalid extension segment '+str(tid))
        by_tank[tid].append((a,b))
    for tank in snapshot['tanks']:
        tid=tank['tank_id'];cursor=start
        for a,b in sorted(by_tank[tid]):
            if a!=cursor:raise ValueError('Flow gap or overlap '+tid)
            cursor=b
        if cursor!=end:raise ValueError('Incomplete extension coverage '+tid)
    if timestamp(flow['flow_horizon_end_at'])!=end:raise ValueError('Extension end mismatch')
    profile=dict(PROFILE,segments=n+len(extra),flow_end=end.isoformat())
    return validate(snapshot,profile)


def check_extension_available(snapshot, *, as_of_at, projection_end_at):
    """Reject use of extension before its recorded information availability."""
    meta=snapshot['fixed_process_flows'].get('extension_provenance')
    if meta and timestamp(projection_end_at)>timestamp(meta['extension_start_at']) and timestamp(as_of_at)<timestamp(meta['known_at']):
        raise ValueError('FUTURE_INFORMATION_LEAKAGE: extension is not yet known at planning time')
