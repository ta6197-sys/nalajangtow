#!/usr/bin/env python3
"""Read-only triage of V5 window_003_failure.json (not a solver feasibility proof).

Uses saved failure evidence only; optional normalized snapshot and policy provide
ETA and earliest ETB calculations for each eligible berth. No inputs mutated.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sys

DEFAULT_CALLS = ('DEM-0030', 'DEM-0084')


def _read(path: Path) -> tuple[dict, str]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 150_000_000:
        raise ValueError('Missing, symlinked, or oversized source: ' + str(path))
    content = path.read_bytes()
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError('Expected JSON object')
    return result, sha256(content).hexdigest()


def _at(value):
    value = datetime.fromisoformat(value)
    if value.utcoffset() is None:
        raise ValueError('Timezone-aware timestamps are required')
    return value


def diagnose(evidence: dict, ids: tuple[str, ...], *, snapshot=None, policy=None) -> dict:
    if evidence.get('validated') is True:
        raise ValueError('Passed evidence is not failed-window evidence')
    if 'window_id' not in evidence or 'selected_calls_with_zero_candidates' not in evidence:
        raise ValueError('Unsupported evidence format; expected build_failure_evidence output')
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('Duplicate or missing call IDs')
    out = {
        'status': 'READ_ONLY_FINITE_DOMAIN_TRIAGE',
        'window_id': evidence['window_id'],
        'as_of_at': evidence.get('as_of_at'),
        'triage_classification': evidence.get('triage_classification'),
        'solver_status': evidence.get('solver_status'),
        'stopping_reason': evidence.get('stopping_reason'),
        'candidate_domain_fingerprint': evidence.get('candidate_domain_fingerprint'),
        'rejection_records_truncated': evidence.get('rejection_records_truncated'),
        'physical_infeasibility_proven': False,
        'calls': {},
    }
    rows = evidence.get('rejection_records') or []
    if not isinstance(rows, list):
        raise ValueError('Malformed rejection_records')
    source_calls = {c['call_id']: c for c in (snapshot or {}).get('calls', [])}
    for cid in ids:
        rel = [r for r in rows if isinstance(r, dict) and r.get('call_id') == cid]
        reasons = Counter((str(x.get('code','UNKNOWN')),str(x.get('reason','UNKNOWN'))) for x in rel)
        counts = dict((evidence.get('domain_counts_by_call_and_berth') or {}).get(cid) or {})
        item = {
            'in_selected_scope': cid in (evidence.get('selected_call_ids') or []),
            'zero_generated_valid_options': cid in (evidence.get('selected_calls_with_zero_candidates') or []),
            'accepted_options_by_berth': counts,
            'accepted_option_total': (evidence.get('domain_counts_by_call') or {}).get(cid),
            'rejection_record_count_in_saved_sample':len(rel),
            'rejection_reason_counts_in_saved_sample':[{'code':code, 'reason':reason, 'count':n} for (code,reason),n in sorted(reasons.items())],
            'rejection_examples':rel[:12],
        }
        if cid in source_calls:
            c = source_calls[cid]
            item['eta_at'] = c.get('eta_at')
            item['fix_etb_at'] = c.get('fix_etb_at')
            item['fixed_etb_is_hard'] = c.get('fixed_etb_is_hard')
            item['eligible_berth_ids'] = c.get('eligible_berth_ids')
            if policy and evidence.get('as_of_at') and c.get('eta_at'):
                begin = _at(evidence['as_of_at'])
                eta = _at(c['eta_at'])
                item['earliest_etb_by_berth'] = {}
                for bid in c.get('eligible_berth_ids') or []:
                    b = policy.get('berths',{}).get(bid)
                    if not isinstance(b,dict):continue
                    seconds = b.get('berthing_seconds')
                    offset = b.get('pilot_boarding_to_berthing_seconds',0)
                    if not isinstance(seconds,int) or not isinstance(offset,int):continue
                    maneuver = timedelta(seconds=seconds+offset)
                    etb = max(begin,eta) + maneuver
                    item['earliest_etb_by_berth'][bid] = {
                        'earliest_etb_from_eta_and_berthing_at':etb.isoformat(),
                        'earliest_pob':max(begin,eta).isoformat(),
                    }
        out['calls'][cid] = item
    out['interpretation'] = (
        'A zero validated-option count proves shortage in the GENERATED FINITE '
        'candidate domain only. ETA/POB or pilotage rejection of some anchors '
        'does not prove that no feasible berth/time exists in continuous time.'
    )
    out['next_checks'] = [
        'Identify every eligible berth and all generated ETB anchors for both calls.',
        'For each rejected anchor, compare actual POB and ETA, 72h window boundary, daylight/pilotage windows, and work restrictions.',
        'Check if a later valid pilotage interval lies inside the 72h horizon but is missing from candidate sampling.',
        'Separate finite-domain infeasibility, missing policy, and shared solver time exhaustion; do not auto-defer unresolved cases.',
        'Only after finding a reproducible generator defect, fix it in a separate PR with regression tests.',
    ]
    if evidence.get('rejection_records_truncated'):
        out['sample_warning'] = 'Saved rejection examples are truncated; counts may exceed grouped samples.'
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--call-id', action='append', dest='ids')
    parser.add_argument('--snapshot', type=Path, help='Optional normalized V5 snapshot JSON')
    parser.add_argument('--policy', type=Path, help='Optional V5 policy JSON')
    parser.add_argument('--out', type=Path, help='Optional new report JSON (must not already exist)')
    args = parser.parse_args(argv)
    try:
        evidence, evidence_sha = _read(args.evidence)
        snapshot = _read(args.snapshot)[0] if args.snapshot else None
        policy = _read(args.policy)[0] if args.policy else None
        if policy is not None and snapshot is None:
            raise ValueError('For policy-derived earliest ETB, also supply normalized --snapshot')
        result = diagnose(evidence, tuple(args.ids or DEFAULT_CALLS), snapshot=snapshot, policy=policy)
        result['evidence_sha256'] = evidence_sha
        text = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n'
        if args.out:
            if args.out.is_symlink() or args.out.exists():
                raise ValueError('Report destination already exists (will not overwrite): ' + str(args.out))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open('x',encoding='utf-8') as f:
                f.write(text)
        print(text, end='')
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({'status':'BLOCKED', 'reason':str(exc)}, ensure_ascii=False),file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
