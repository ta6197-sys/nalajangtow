"""Validate explicitly supplied, fully released historical physical rows."""
from ..integrated_input.validator import timestamp
from .validator import validate_result
from .v5_user_rules import VERSION as USER_V5_VERSION


def completed_call_ids(snapshot, rows, policy, *, as_of_at, locked_ids=()):
    """Rows must already be released; this function never infers observations."""
    boundary=timestamp(as_of_at);rows=list(rows);ids=[row['call_id'] for row in rows]
    if len(ids)!=len(set(ids)) or set(ids)&set(locked_ids):raise ValueError('Duplicate or simultaneously locked completed call')
    checked=validate_result(snapshot,rows,policy)
    if checked['status']!='VALID':raise ValueError('Completed physical rows are invalid: '+str(checked['issues']))
    for row in rows:
        if ((timestamp(row['etu_at'])>boundary) if row.get('phase_policy_version')==USER_V5_VERSION else (max(timestamp(row['physical_clearance_at']),timestamp(row['switching_end_at']))>boundary)):
            raise ValueError('Completed call still occupies a physical or switching resource: '+row['call_id'])
    return set(ids)
