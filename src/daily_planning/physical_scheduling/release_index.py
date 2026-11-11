"""Exact pre-index of existing berth/dock phase-release ETB anchors.

Only the lookup is accelerated. No release timestamp is discarded, and
same-call releases are excluded exactly as in the original nested scan.
"""
from bisect import bisect_right
from collections import defaultdict
from ..integrated_input.validator import timestamp


class ReleaseEventIndex:
    """Index release events for candidate replays without scanning all options."""

    def __init__(self, options, commitments, policy, phase_release_policy):
        if phase_release_policy not in ('ALL_OPTIONS_V1', 'LOCKED_ONLY_V1'):
            raise ValueError('Unknown phase release policy')
        self.dock = frozenset(policy.get('dock_4142_berths', ()))
        events = defaultdict(lambda: defaultdict(set))
        sources = list(commitments) if phase_release_policy == 'LOCKED_ONLY_V1' else list(options) + list(commitments)
        for row in sources:
            berth = row['berth_id']
            scope = ('DOCK_4142',) if berth in self.dock else ('BERTH', berth)
            events[scope][timestamp(row['switching_end_at'])].add(row['call_id'])
        self.events = {
            scope: (sorted(values), values)
            for scope, values in events.items()
        }

    def later_releases(self, option):
        """Yield exactly the original sorted unique release timestamps."""
        berth = option['berth_id']
        scope = ('DOCK_4142',) if berth in self.dock else ('BERTH', berth)
        times, call_ids = self.events.get(scope, ((), {}))
        etb = timestamp(option['etb_at'])
        for moment in times[bisect_right(times, etb):]:
            # At least one OTHER vessel must provide the release event.
            owners = call_ids[moment]
            if len(owners) > 1 or option['call_id'] not in owners:
                yield moment.isoformat()
