"""Conservative candidate-pair prefilter; does not replace physical checks."""
from ..integrated_input.validator import timestamp,tokens

VERSION='SAFE_ENVELOPE_V1'


def build_conflict_index(snapshot, policy, rows):
    """Return a necessary (not sufficient) condition for pair conflicts.

    Every enforced phase lies within berthing-start through the later of
    physical clearance and switching end. Same berth and every cross-berth
    group/shared resource are retained. Boundaries are half-open.
    """
    pairs=set()
    for group in snapshot['workbook_tables']['groups']['rows']:
        members=tokens(group['berth_ids'])
        for a in members:
            for b in members:pairs.add((a,b))
    dock=policy.get('dock_4142_berths',[])
    for a in dock:
        for b in dock:pairs.add((a,b))
    envelopes=[(timestamp(row['berthing_start_at']),max(timestamp(row['etu_at']),timestamp(row['switching_end_at']))) for row in rows]
    def potentially_conflicting(i,j):
        a,b=rows[i],rows[j]
        if a['call_id']==b['call_id']:return False
        if a['berth_id']!=b['berth_id'] and (a['berth_id'],b['berth_id']) not in pairs:return False
        x,y=envelopes[i],envelopes[j]
        return max(x[0],y[0])<min(x[1],y[1])
    return potentially_conflicting


def iter_potential_pairs(snapshot, policy, rows, *, check_deadline=lambda: None):
    """Sweep overlapping half-open envelopes while preserving all possible pairs."""
    potential=build_conflict_index(snapshot,policy,rows)
    envelopes=[(timestamp(row['berthing_start_at']),max(timestamp(row['etu_at']),timestamp(row['switching_end_at']))) for row in rows]
    order=sorted(range(len(rows)),key=lambda i:(envelopes[i][0],i));active=[]
    for i in order:
        check_deadline();begin=envelopes[i][0]
        active=[j for j in active if envelopes[j][1]>begin]
        for n,j in enumerate(active):
            if n%128==0:check_deadline()
            if potential(j,i):yield (j,i) if j<i else (i,j)
        active.append(i)
