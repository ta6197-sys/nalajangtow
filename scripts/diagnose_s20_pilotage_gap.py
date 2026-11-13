#!/usr/bin/env python3
"""Read-only S20 window-003 pilotage gap proof against verified 154-call inputs.

No schedule is solved, no existing checkpoints or inputs are modified.
If an evidence path is provided, its SHA256 and original triage are recorded,
not silently treated as proof for a separate source/configuration.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.daily_planning.integrated_v3.v5_scale import load_execution_config,preflight_sources
from src.daily_planning.integrated_v3.v5_pilotage_gap import collect_certificates


def read_failure(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>20_000_000:
        raise ValueError('Missing/symlinked/oversized evidence source')
    b=path.read_bytes();v=json.loads(b)
    if not isinstance(v,dict) or v.get('window_id')!=3 or v.get('validated') is not False:
        raise ValueError('Expected raw failed window-003 evidence')
    return {'source_evidence_sha256':hashlib.sha256(b).hexdigest(),
            'original_triage_classification':v.get('triage_classification'),
            'original_zero_option_ids':v.get('selected_calls_with_zero_candidates',[]),
            'source_fingerprint':v.get('source_fingerprint'),
            'policy_fingerprint':v.get('policy_fingerprint')}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True,type=Path)
    p.add_argument('--as-of-at',default='2026-11-05T00:00:00+09:00')
    p.add_argument('--call-id',action='append',default=None)
    p.add_argument('--failure-evidence',type=Path)
    p.add_argument('--out',type=Path,help='New JSON path; refuse overwrite')
    a=p.parse_args(argv)
    try:
        config=load_execution_config(a.config)
        snapshot, preflight=preflight_sources(config)
        if snapshot.get('scenario_id')!='V5_BALANCED_154':
            raise ValueError('Require verified BALANCED 154 inputs')
        ids=a.call_id or ['DEM-0030','DEM-0084']
        result=collect_certificates(snapshot,ids,as_of_at=a.as_of_at,
                                    policy=config['policy'],pilotage_context=config['pilotage_context'])
        record={'version':'V5_S20_PILOTAGE_GAP_AUDIT_V1',
                'classification':'PROVED_NO_CURRENT_HORIZON_PILOTAGE_SLOT_ONLY',
                'source_profile_id':snapshot.get('source_profile_id',snapshot.get('scenario_id')),
                'source_preflight':preflight.get('status'),
                'as_of_at':a.as_of_at,'examined_calls':ids,
                'certified_gap_call_ids':sorted(result['certificates']),
                'certificates':result['certificates'],'unresolved':result['unresolved'],
                'other_constraints_including_inventory':'NOT_EVALUATED',
                'solver_status':'NOT_RUN','next_window_scheduling':'NOT_PROVEN',
                'operational_approval':False}
        if a.failure_evidence:record['original_failure_evidence']=read_failure(a.failure_evidence)
        data=json.dumps(record,ensure_ascii=False,indent=2,sort_keys=True)+'\n'
        if a.out:
            if a.out.is_symlink() or a.out.exists():raise ValueError('Report path already exists: '+str(a.out))
            a.out.parent.mkdir(parents=True,exist_ok=True)
            with a.out.open('x',encoding='utf-8') as f:f.write(data)
        print(data,end='')
        return 0 if len(result['certificates'])==len(ids) else 2
    except (OSError,KeyError,ValueError,TypeError,ImportError,json.JSONDecodeError) as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2
if __name__=='__main__':sys.exit(main())
