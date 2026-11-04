#!/usr/bin/env python3
"""Review-first SK 1-8 SINGLE-BERTH V3 patches to existing planning code.

Refuses drifted snippets.  Does not touch PostgreSQL or past run files.
Run --check first; --apply backs up every modified source under ignored results/.
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def one(content, before, after, label):
    n = content.count(before)
    if n != 1:
        raise ValueError(f"{label}: expected exact source pattern once; found {n}. STOP and review manually.")
    return content.replace(before, after, 1)


def change_context(content):
    content = one(content,
        "    snapshot['snapshot_hash'] = signature(snapshot)\n    return snapshot\n",
        "    # Step3: opt-in documentary SK physical filtering; legacy snapshots unchanged.\n"
        "    from .sk_physical_v3 import build_sk_snapshot_for_experiment\n"
        "    physical = build_sk_snapshot_for_experiment(experiment_key, snapshot['calls'], snapshot['berths'])\n"
        "    if physical is not None:\n"
        "        snapshot['sk_physical'] = physical\n"
        "    snapshot['snapshot_hash'] = signature(snapshot)\n    return snapshot\n",
        "context.read_snapshot")
    content = one(content,
        "    if snapshot.get('daylight_windows') and fingerprint(snapshot['daylight_windows']['path']) != snapshot['daylight_windows']:\n"
        "        raise ValueError('Sunrise/sunset file differs from the frozen input')\n",
        "    if snapshot.get('daylight_windows') and fingerprint(snapshot['daylight_windows']['path']) != snapshot['daylight_windows']:\n"
        "        raise ValueError('Sunrise/sunset file differs from the frozen input')\n"
        "    if snapshot.get('sk_physical'):\n"
        "        from .sk_physical_v3 import verify_sk_snapshot\n"
        "        verify_sk_snapshot(snapshot['sk_physical'])\n",
        "context.verify_snapshot")
    return content


def change_exact_search(content):
    content = one(content,
        "from .evaluation import evaluate_baseline, interval_overlap, price_until\n",
        "from .evaluation import evaluate_baseline, interval_overlap, price_until\n"
        "from .sk_physical_v3 import eligible_berths, assert_eligible\n",
        "exact_search imports")
    content = one(content,
        "    for c in snapshot['commits']:\n        row = new_simulate(by_call[c['call_id']],local_time(c['start_at']),weather,RULE)\n",
        "    for c in snapshot['commits']:\n        assert_eligible(snapshot, c['call_id'], c['berth_id'])\n        row = new_simulate(by_call[c['call_id']],local_time(c['start_at']),weather,RULE)\n",
        "exact_search fixed committed berth")
    content = one(content,
        "            for bid in berths:\n                assigned = dict(row,berth_id=bid)\n",
        "            for bid in eligible_berths(snapshot, cid, berths):\n"
        "                assigned = dict(row,berth_id=bid)\n",
        "exact_search physical berth options")
    return content


def change_evaluation(content):
    content = one(content,
        "from .daylight import WORK_POLICY, daylight_window, simulator_for_policy\n",
        "from .daylight import WORK_POLICY, daylight_window, simulator_for_policy\n"
        "from .sk_physical_v3 import eligible_berths, assert_eligible\n",
        "evaluation imports")
    content = one(content,
        "        row = replay_row(calls[cid], commit['berth_id'], commit['start_at'], weather, committed=True, policy=policy)\n",
        "        assert_eligible(snapshot, cid, commit['berth_id'])\n"
        "        row = replay_row(calls[cid], commit['berth_id'], commit['start_at'], weather, committed=True, policy=policy)\n",
        "evaluation committed physical berth")
    content = one(content,
        "        row = replay_row(calls[cid], bid, start, weather, daylight=daylight, policy=policy)\n",
        "        assert_eligible(snapshot, cid, bid)\n"
        "        row = replay_row(calls[cid], bid, start, weather, daylight=daylight, policy=policy)\n",
        "evaluation planned physical berth")
    content = one(content,
        "        bid = min(active_berths, key=lambda b: (free[b], b))\n",
        "        compatible = eligible_berths(snapshot, cid, active_berths)\n"
        "        if not compatible:\n"
        "            raise ValueError(f'No eligible evaluation-tail berth for {cid}')\n"
        "        bid = min(compatible, key=lambda b: (free[b], b))\n",
        "evaluation FCFS tail physical berth")
    return content


def change_execution(content):
    content = one(content,
        "from .evaluation import price_until,replay_row,interval_overlap\n",
        "from .evaluation import price_until,replay_row,interval_overlap\n"
        "from .sk_physical_v3 import assert_eligible\n",
        "execution imports")
    content = one(content,
        "        if protected and local_time(protected['start_at'])<cut:\n"
        "            row=replay_row(call,protected['berth_id'],protected['start_at'],weather,policy=snapshot.get('berthing_policy'))\n",
        "        if protected and local_time(protected['start_at'])<cut:\n"
        "            assert_eligible(snapshot, cid, protected['berth_id'])\n"
        "            row=replay_row(call,protected['berth_id'],protected['start_at'],weather,policy=snapshot.get('berthing_policy'))\n",
        "execution confirmed physical berth")
    return content


def change_operations(content):
    content = one(content,
        "    for key in ('calls','contracts','berths','call_states','source_state_ids','weather'):\n"
        "        if operation_input[key]!=frozen[key]:\n"
        "            raise ValueError('Operating state changed after confirmation; operation must not overwrite it')\n",
        "    for key in ('calls','contracts','berths','call_states','source_state_ids','weather'):\n"
        "        if operation_input[key]!=frozen[key]:\n"
        "            raise ValueError('Operating state changed after confirmation; operation must not overwrite it')\n"
        "    if operation_input.get('sk_physical') != frozen.get('sk_physical'):\n"
        "        raise ValueError('SK physical input changed after confirmation')\n",
        "operations advancing physical input consistency")
    return content


def change_optimization(content):
    content = one(content,
        "        stability_enabled=True,pending=[])\n    prepared=[]\n",
        "        stability_enabled=True,pending=[])\n"
        "    if snapshot.get('sk_physical'):\n"
        "        summary['vessel_data_origin'] = 'SYNTHETIC'\n"
        "        summary['sk_physical_scope'] = snapshot['sk_physical']['scope']\n"
        "        summary['sk_not_evaluated'] = snapshot['sk_physical']['not_evaluated']\n"
        "    prepared=[]\n",
        "optimization synthetic disclosure")
    return content


def change_web_app(content):
    content = one(content,
        "        candidates.append(dict(rank=row['candidate_rank'], label='ABC'[row['candidate_rank'] - 1],\n",
        "        if snapshot.get('sk_physical'):\n"
        "            for vessel_row in vessel_results:\n"
        "                vessel_row['data_origin'] = 'SYNTHETIC'\n"
        "                vessel_row['vessel_profile'] = snapshot['sk_physical']['profiles'].get(vessel_row['call_id'])\n"
        "        candidates.append(dict(rank=row['candidate_rank'], label='ABC'[row['candidate_rank'] - 1],\n",
        "web_app per-vessel synthetic provenance")
    content = one(content,
        "                completed_count=len(operation['completed_call_ids']) if operation else None)\n",
        "                completed_count=len(operation['completed_call_ids']) if operation else None,\n"
        "                vessel_data_origin=snapshot.get('sk_physical', {}).get('data_origin'),\n"
        "                physical_constraint_scope=snapshot.get('sk_physical', {}).get('scope'),\n"
        "                physical_not_evaluated=snapshot.get('sk_physical', {}).get('not_evaluated', []))\n",
        "web_app explicit synthetic data notice")
    return content


PATCHES = {
    "src/daily_planning/context.py": change_context,
    "src/daily_planning/exact_search.py": change_exact_search,
    "src/daily_planning/evaluation.py": change_evaluation,
    "src/daily_planning/execution.py": change_execution,
    "src/daily_planning/optimization.py": change_optimization,
    "src/daily_planning/operations.py": change_operations,
    "web_app.py": change_web_app,
}


def main():
    p = argparse.ArgumentParser(description="Integrate SK physical V3; read-only check unless --apply")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = p.parse_args()
    if not (ROOT / "src/daily_planning/sk_preflight.py").exists():
        raise SystemExit("Missing Step2 src/daily_planning/sk_preflight.py; merge Step2 first")
    if not (ROOT / "src/daily_planning/sk_physical_v3.py").exists():
        raise SystemExit("Missing Step3 src/daily_planning/sk_physical_v3.py")
    new_content = {}
    for name, transform in PATCHES.items():
        path = ROOT / name
        before = path.read_text(encoding="utf-8")
        if "Step3:" in before or "from .sk_physical_v3 import" in before or "vessel_data_origin=snapshot.get('sk_physical'" in before:
            raise SystemExit(f"Patch already present or partially applied in {name}; review git diff")
        after = transform(before)
        compile(after, str(path), "exec")
        new_content[name] = (before, after)
    print(f"Validated {len(new_content)} source patches against current project, no DB migration")
    if args.check:
        print("CHECK OK; run with --apply after reviewing docs")
        return
    backup = ROOT / "results" / ("sk_step3_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    backup.mkdir(parents=True, exist_ok=False)
    for name, (before, _) in new_content.items():
        target = backup / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(before, encoding="utf-8")
    try:
        for name, (_, after) in new_content.items():
            (ROOT / name).write_text(after, encoding="utf-8")
    except Exception:
        for name, (before, _) in new_content.items():
            (ROOT / name).write_text(before, encoding="utf-8")
        raise
    print(f"APPLIED {len(new_content)} source patches. Backups: {backup}")
    print("Review with: git diff --stat && git diff -- src/daily_planning web_app.py")


if __name__ == "__main__":
    main()
