"""Read-only, replaceable physical facility master with source provenance.

This module does not map virtual berths or authorize a physical operation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path



MASTER_VERSION = 'PHYSICAL_FACILITY_MASTER_V1'


def build_facility_master(path, *, active_facility_codes):
    """Load the selected facilities from a named source; never assume active ones."""
    if not isinstance(active_facility_codes, (list, tuple)) or not active_facility_codes:
        raise ValueError('Explicit active facility codes are required')
    if any(not isinstance(code, str) or not code.strip() or code != code.strip() for code in active_facility_codes):
        raise ValueError('Invalid active facility code')
    if len(set(active_facility_codes)) != len(active_facility_codes):
        raise ValueError('Duplicate active facility code')
    source_path = Path(path)
    raw = source_path.read_bytes()
    payload = json.loads(raw)
    if payload.get('schema_version') != 1 or not isinstance(payload.get('facilities'), list):
        raise ValueError('Unsupported facility data schema')
    facilities = {}
    for row in payload['facilities']:
        if not isinstance(row, dict):
            raise ValueError('Invalid facility record')
        code = row.get('facility_code')
        if not isinstance(code, str) or not code.startswith(('SKB#', 'SK#')) or code in facilities:
            raise ValueError('Invalid or duplicate facility code')
        facilities[code] = row
    if not payload.get('source_workbook_name') or not payload.get('source_workbook_sha256') or not payload.get('source_sheet'):
        raise ValueError('Facility source provenance is missing')
    missing = sorted(set(active_facility_codes) - facilities.keys())
    if missing:
        raise ValueError('Unknown facility codes: ' + ', '.join(missing))
    selected = []
    for code in sorted(active_facility_codes):
        row = facilities[code]
        if not isinstance(row.get('workbook_row'), int) or row['workbook_row'] <= 0:
            raise ValueError(f'Missing source row for {code}')
        selected.append(dict(row))
    return {'policy_version': MASTER_VERSION,
            'source': {'data_sha256': hashlib.sha256(raw).hexdigest(),
                       'source_workbook_name': payload['source_workbook_name'],
                       'source_workbook_sha256': payload['source_workbook_sha256'],
                       'source_sheet': payload['source_sheet'],
                       'source_workbook_verified': False},
            'active_facilities': selected,
            'pending_rules': ['PILOTAGE', 'TUG_REQUIREMENTS', 'SWITCHING', 'CROSS_BERTH_OVERLAP',
                              'FIX_ELIGIBILITY', 'TANK_CONNECTIONS'],
            'operational_approval': False}
