"""Measure only synthetic pair-index construction (not CP-SAT or HiGHS)."""
import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from time import perf_counter
from src.daily_planning.physical_scheduling.conflict_index import (
    build_conflict_index, iter_potential_pairs)


def sample(count):
    rnd = random.Random(42)
    start = datetime(2026, 11, 3, tzinfo=timezone(timedelta(hours=9)))
    berths = ['B' + str(i) for i in range(1, 12)]
    rows = []
    for _ in range(count):
        a = start + timedelta(minutes=rnd.randrange(0, 72 * 60))
        b = a + timedelta(minutes=rnd.randrange(40, 150))
        rows.append({
            'call_id': 'C' + str(rnd.randrange(max(1, count // 10))),
            'berth_id': rnd.choice(berths),
            'berthing_start_at': a.isoformat(),
            'etb_at': (a + timedelta(minutes=1)).isoformat(),
            'etu_at': b.isoformat(),
            'switching_end_at': (b + timedelta(minutes=20)).isoformat(),
        })
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--options', type=int, default=1300)
    args = p.parse_args()
    if not 0 <= args.options <= 5000:
        p.error('--options must be 0..5000')
    rows = sample(args.options)
    snapshot = {'workbook_tables': {'groups': {'rows': [
        {'berth_ids': 'B1;B2;B3'}, {'berth_ids': 'B4;B5'}]}}}
    policy = {'dock_4142_berths': ['B6', 'B7']}
    t0 = perf_counter()
    predicate = build_conflict_index(snapshot, policy, rows)
    old = {(i, j) for i in range(len(rows)) for j in range(i + 1, len(rows))
           if predicate(i, j)}
    old_seconds = perf_counter() - t0
    t0 = perf_counter()
    new = set(iter_potential_pairs(snapshot, policy, rows))
    new_seconds = perf_counter() - t0
    if old != new:
        raise AssertionError('Candidate conflict-domain mismatch')
    print(json.dumps({
        'scope': 'SYNTHETIC_PAIR_PREFILTER_ONLY_NOT_FULL_SOLVER',
        'options': len(rows), 'potential_pairs': len(new),
        'full_scan_seconds': round(old_seconds, 6),
        'indexed_sweep_seconds': round(new_seconds, 6),
        'domain_equal': True
    }, indent=2))


if __name__ == '__main__':
    main()
