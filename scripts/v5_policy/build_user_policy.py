#!/usr/bin/env python3
"""Create versioned V5 user-policy config; never modify the original source data.

Usage: python scripts/v5_policy/build_user_policy.py --base path/to/current_policy.json \
       --output path/to/v5_user_policy.json --source-reference USER_RULES_2026-11-12
The preexisting hose_connection_seconds is retained unchanged and remains SYNTHETIC.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.daily_planning.physical_scheduling.v5_user_rules import (
    BERTH_RULES, DOCK_VERSION, VERSION, HOSE_DISCONNECT_SECONDS,
    verify_v5_policy,
)


def build_policy(base, source_reference):
    if not isinstance(base, dict) or not isinstance(base.get('berths'), dict):
        raise ValueError('Base policy with berth phases is required')
    if set(base['berths']) != set(BERTH_RULES):
        raise ValueError('Base policy must explicitly contain the 11 V5 berths')
    if not isinstance(source_reference, str) or not source_reference.strip():
        raise ValueError('Source reference required')
    result = json.loads(json.dumps(base))
    result['schema_version'] = VERSION
    result['dock_4142_version'] = DOCK_VERSION
    result['dock_4142_berths'] = ['BERTH-07', 'BERTH-08']
    result['hose_disconnect_seconds'] = HOSE_DISCONNECT_SECONDS
    result['data_origin'] = 'SYNTHETIC_FIXTURE'
    result['operational_approval'] = False
    result['source_reference'] = source_reference
    # Shared resources are exactly the two documented 4-1/4-2 resources.
    # Their pairwise constraints are solved in v5_user_rules, NOT by blanket
    # no-overlap of all phases or by merging independent berth identities.
    result['dock_4142_shared_resources'] = {
        'cargo': ['CARGO'],
        'hose_connect_disconnect': ['HOSE_CONNECT', 'HOSE_DISCONNECT'],
    }
    result['dock_4142_berthing_resource'] = 'INDEPENDENT'
    result['event_definition_resolution'] = 'EXPLICIT_USER_20261112_V1'
    # Not a phase: stored only as a rule for conditional *between-call* release.
    for bid, (reference, direction, switching) in BERTH_RULES.items():
        cfg = result['berths'][bid]
        cfg.update(berthing_seconds=direction, direction_seconds=direction,
                   switching_seconds=switching, unberthing_seconds=0,
                   pilot_boarding_to_berthing_seconds=0,
                   pilot_boarding_to_unberthing_seconds=0,
                   reference_berth_label=reference)
        # An old, call-specific outbound restriction is NOT inherited.
        cfg.pop('pilotage_windows_by_call', None)
    verify_v5_policy(result, list(BERTH_RULES))
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--base', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--source-reference', required=True)
    args = p.parse_args()
    source, target = Path(args.base), Path(args.output)
    if source.resolve() == target.resolve() or target.exists():
        p.error('Refusing to overwrite an existing file or the base source')
    data = json.loads(source.read_text(encoding='utf-8'))
    result = build_policy(data, args.source_reference)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Wrote {target} (simulation-only, user rule version {VERSION})')


if __name__ == '__main__':
    main()
