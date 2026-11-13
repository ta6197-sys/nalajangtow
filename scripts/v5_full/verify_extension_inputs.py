#!/usr/bin/env python3
"""Independent read-only validation of this pinned 154-call/816-flow synthetic pair.

This is a source integrity checker, not a scheduling or tank feasibility proof.
Optional --legacy-flow proves the 696 source segments are the 816 prefix.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime,timedelta
from hashlib import sha256
import json
from pathlib import Path
import sys

SOURCE_NAME='Docktimize_v5_선박입력_BALANCED.json'
FLOW_NAME='Docktimize_공정유량_6h_3탱크_1000만bbl_BALANCED_20270109.json'
SOURCE_SHA='4d587107a051e83bcc3b9e80e74cbd17585e2d2b555eac1dc5fcb5faae2a7850'
FLOW_SHA='cabe3a7eab2d1e20333c02eb1d40dfb09b58796d7c4d865eb649994b426fbfe2'
PARENT_SHA='9c0d3ee063f54f93af03c122b1d8ee9608ee1a6e6e36c34ba3bdb3589c6ba763'
START=datetime.fromisoformat('2026-11-03T00:00:00+09:00')
END=datetime.fromisoformat('2027-01-10T00:00:00+09:00')
TANKS={'TANK-01','TANK-02','TANK-03'}


def unique(pairs):
    obj={}
    for k,v in pairs:
        if k in obj:raise ValueError('duplicate JSON key: '+str(k))
        obj[k]=v
    return obj


def load(path):
    raw=Path(path).read_bytes()
    return json.loads(raw,parse_float=str,object_pairs_hook=unique),sha256(raw).hexdigest()


def ordered_flow(flow,expected_count,until):
    by=defaultdict(list)
    for row in flow['segments']:
        if row['tank_id'] not in TANKS:raise ValueError('unexpected tank in flow')
        by[row['tank_id']].append(row)
    if set(by)!=TANKS:raise ValueError('missing tank flow')
    for tid in sorted(TANKS):
        rows=sorted(by[tid],key=lambda x:x['start_at'])
        if len(rows)!=expected_count:raise ValueError(f'{tid} expected {expected_count}, got {len(rows)}')
        t=START
        for row in rows:
            a=datetime.fromisoformat(row['start_at']);b=datetime.fromisoformat(row['end_at'])
            if a!=t or b!=a+timedelta(hours=6):raise ValueError(f'gap, overlap, or wrong duration: {tid} at {t}')
            t=b
        if t!=until:raise ValueError(f'{tid} ends at {t}, expected {until}')
        by[tid]=rows
    return by


def verify(folder:Path,legacy=None):
    s,sh=load(folder/SOURCE_NAME);f,fh=load(folder/FLOW_NAME)
    if (sh,fh)!=(SOURCE_SHA,FLOW_SHA):raise ValueError('Fixture byte SHA mismatch; do not silently accept changed input')
    if s.get('schema_version')!='DOCKTIMIZE_COORDINATOR_SYNTHETIC_INPUT_UNASSIGNED_V5':raise ValueError('wrong source schema')
    if f.get('schema_version')!='FIXED_PROCESS_FLOW_TS_V1':raise ValueError('wrong flow schema')
    if s.get('process_flow_file')!=FLOW_NAME:raise ValueError('source refers to different flow name')
    for key in ('seed','timezone','data_origin','operational_approval'):
        if s.get(key)!=f.get(key):raise ValueError('source / flow provenance mismatch: '+key)
    if s.get('operational_approval') is not False or s.get('data_origin')!='SYNTHETIC_FIXTURE':raise ValueError('not a synthetic source')
    calls=s.get('cargo_calls',[])
    if len(calls)!=154 or len(s.get('vessel_profiles',[]))!=154 or len(s.get('berths',[]))!=11 or len(s.get('tanks',[]))!=3:raise ValueError('unexpected source counts')
    ids=[c.get('call_id') for c in calls]
    if len(set(ids))!=154:raise ValueError('duplicate call')
    if sum(c.get('fixed_etb_is_hard') is True for c in calls)!=15:raise ValueError('FIX ETB count differs')
    if f.get('record_count')!=816 or f.get('tank_count')!=3 or f.get('interval_hours')!=6:raise ValueError('flow header count mismatch')
    if datetime.fromisoformat(f['inventory_as_of_at'])!=START or datetime.fromisoformat(f['flow_horizon_end_at'])!=END:raise ValueError('flow horizon mismatch')
    for x in s['tanks']:
        if x.get('tank_id') not in TANKS:raise ValueError('unknown tank')
        if str(x['capacity_bbl'])!='10000000':raise ValueError('tank capacity changed')
    parent=s.get('balanced_enrichment_provenance',{}).get('external_flow_json_sha256')
    if parent!=PARENT_SHA:raise ValueError('unexpected parent-flow provenance')
    ext=ordered_flow(f,272,END)
    prefix='NOT_VERIFIED_ORIGINAL_FLOW_FILE_NOT_SUPPLIED'
    if legacy:
        old,old_sha=load(legacy)
        if old_sha!=PARENT_SHA:raise ValueError('supplied original flow does not match historical SHA')
        base=ordered_flow(old,232,datetime.fromisoformat('2026-12-31T00:00:00+09:00'))
        for tid in sorted(TANKS):
            if ext[tid][:232]!=base[tid]:raise ValueError('historical 696 segments differ from extended prefix: '+tid)
        prefix='VERIFIED_EXACT_696_SEGMENT_PREFIX'
    return {'status':'SOURCE_STRUCTURAL_PASS','source_sha256':sh,'extended_flow_sha256':fh,
            'historical_parent_flow_sha256':parent,'original_prefix_comparison':prefix,
            'calls':154,'hard_fix_count':15,'berths':11,'tanks':3,'flow_segments':816,
            'segments_per_tank':272,'flow_end_exclusive':END.isoformat(),
            'optimization':'NOT_RUN','tank_replay':'NOT_RUN','policy_coverage':'NOT_RUN'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input-dir',type=Path,required=True)
    p.add_argument('--legacy-flow',type=Path,help='original 696-segment flow file to verify prefix')
    a=p.parse_args()
    try:print(json.dumps(verify(a.input_dir,a.legacy_flow),ensure_ascii=False,indent=2))
    except (ValueError,KeyError,TypeError,OSError) as e:
        print(json.dumps({'status':'INPUT_INVALID','reason':str(e)},ensure_ascii=False),file=sys.stderr)
        sys.exit(2)
