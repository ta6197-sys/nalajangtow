"""Conservative and indexed candidate-pair filter; never replaces replay."""
from collections import defaultdict
from heapq import heappop, heappush
from ..integrated_input.validator import timestamp, tokens

VERSION = 'SAFE_ENVELOPE_V1'


def _interacting_berths(snapshot, policy, rows):
    """A superset of berth pairs that could share a physical resource."""
    known = {row['berth_id'] for row in rows}
    neighbors = {berth: {berth} for berth in known}
    for group in snapshot['workbook_tables']['groups']['rows']:
        members = set(tokens(group['berth_ids'])) & known
        for berth in members:
            neighbors[berth].update(members)
    dock = set(policy.get('dock_4142_berths', ())) & known
    for berth in dock:
        neighbors[berth].update(dock)
    return neighbors


def _envelopes(rows):
    return [(timestamp(row['berthing_start_at']),
             max(timestamp(row['etu_at']), timestamp(row['switching_end_at'])))
            for row in rows]


def build_conflict_index(snapshot, policy, rows):
    """Return a necessary (not sufficient) condition for pair conflicts."""
    neighbors = _interacting_berths(snapshot, policy, rows)
    envelopes = _envelopes(rows)

    def potentially_conflicting(i, j):
        a, b = rows[i], rows[j]
        if a['call_id'] == b['call_id']:
            return False
        if b['berth_id'] not in neighbors[a['berth_id']]:
            return False
        x, y = envelopes[i], envelopes[j]
        return max(x[0], y[0]) < min(x[1], y[1])

    return potentially_conflicting


def iter_potential_pairs(snapshot, policy, rows, *, check_deadline=lambda: None):
    """Sweep only simultaneously active candidates from resource-interacting berths.

    Return exactly the old SAFE_ENVELOPE_V1 candidate-pair domain; independent
    pair_conflicts and final physical replay still enforce actual constraints.
    """
    neighbors = _interacting_berths(snapshot, policy, rows)
    envelopes = _envelopes(rows)
    order = sorted(range(len(rows)), key=lambda i: (envelopes[i][0], i))
    active_by_berth = defaultdict(set)
    expiry = []
    for i in order:
        check_deadline()
        begin, end = envelopes[i]
        while expiry and expiry[0][0] <= begin:
            _, j = heappop(expiry)
            active_by_berth[rows[j]['berth_id']].remove(j)
        n = 0
        for berth in sorted(neighbors[rows[i]['berth_id']]):
            for j in sorted(active_by_berth[berth]):
                if n % 128 == 0:
                    check_deadline()
                n += 1
                if rows[i]['call_id'] != rows[j]['call_id']:
                    yield (j, i) if j < i else (i, j)
        active_by_berth[rows[i]['berth_id']].add(i)
        heappush(expiry, (end, i))
