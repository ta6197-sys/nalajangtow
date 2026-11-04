#!/usr/bin/env python3
"""Create a blank, unverified vessel profile file; never invent vessel dimensions."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.daily_planning.sk_preflight import create_blank_profiles


def main() -> int:
    parser = argparse.ArgumentParser(description="Create blank vessel profiles by call ID")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--call-id", action="append", help="Repeat for several calls")
    group.add_argument("--from-db", action="store_true", help="Read existing call IDs from reachable PostgreSQL DB")
    group.add_argument("--call-ids-file", type=Path, help="Plain text: one call_id per line")
    parser.add_argument("--out", type=Path, default=ROOT / "input/vessel_profiles.local.json")
    args = parser.parse_args()
    try:
        if args.from_db:
            import sqlalchemy as sa
            from src.daily_planning.database import get_engine
            from src.daily_planning.models import port_call
            engine = get_engine()
            with engine.connect() as con:
                ids = list(con.execute(sa.select(port_call.c.call_id).order_by(port_call.c.call_id)).scalars())
            engine.dispose()
        elif args.call_ids_file:
            ids = [line.strip() for line in args.call_ids_file.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        else:
            ids = args.call_id
        if not ids:
            raise ValueError("No call IDs available")
        payload = create_blank_profiles(ids)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"Created {args.out} with {len(ids)} UNVERIFIED calls; enter actual/simulation-supported dimensions manually")
        return 0
    except (OSError, ValueError, ImportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
