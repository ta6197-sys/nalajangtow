
import argparse
import hashlib
import json
import random
from pathlib import Path

# 모두 시뮬레이션용 가상 범위입니다.
# SK의 실제 선박 분포 또는 물리적 상관관계를 뜻하지 않습니다.
SIZE_CLASSES = {
    "SMALL": {
        "dwt": (4000, 12000),
        "loa_m": (85, 135),
        "draft_m": (5.0, 9.0),
    },
    "MEDIUM": {
        "dwt": (12000, 70000),
        "loa_m": (130, 245),
        "draft_m": (7.0, 14.0),
    },
    "LARGE": {
        "dwt": (70000, 160000),
        "loa_m": (230, 285),
        "draft_m": (12.0, 17.0),
    },
}

CARGO_TYPES = ["원유", "석유정제품", "LPG"]


def create_profile(call_id: str, seed: int) -> dict:
    # 항차별 고정 난수: 입력 순서가 바뀌어도 결과 유지
    key = f"{seed}:{call_id}".encode("utf-8")
    stable_seed = int.from_bytes(
        hashlib.sha256(key).digest()[:8], "big"
    )
    rng = random.Random(stable_seed)

    cargo = rng.choice(CARGO_TYPES)
    size_class = rng.choices(
        ["SMALL", "MEDIUM", "LARGE"],
        weights=[45, 40, 15],
        k=1,
    )[0]

    limits = SIZE_CLASSES[size_class]

    dwt = rng.randint(*limits["dwt"])
    loa_m = round(rng.uniform(*limits["loa_m"]), 2)
    draft_m = round(rng.uniform(*limits["draft_m"]), 2)

    # 이 환산 역시 임의의 가상 시뮬레이션 가정
    gt = round(dwt * rng.uniform(0.52, 0.64))

    return {
        "call_id": call_id,
        "vessel_id": f"SYN-{call_id}",
        "cargo_type": cargo,
        "dwt": dwt,
        "loa_m": loa_m,
        "draft_m": draft_m,
        "gt": gt,
        "size_class": size_class,
        "data_origin": "SYNTHETIC",
        "verification_status": "SCENARIO_ASSUMPTION",
        "target_berth_id": None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--call-ids-file", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--expected-count", type=int, default=140)
    args = parser.parse_args()

    call_ids = [
        line.strip()
        for line in Path(args.call_ids_file).read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

    if len(call_ids) != len(set(call_ids)):
        raise ValueError("Duplicate call IDs")

    if len(call_ids) != args.expected_count:
        raise ValueError(
            f"Expected {args.expected_count} calls, "
            f"found {len(call_ids)}"
        )

    profiles = [
        create_profile(cid, args.seed)
        for cid in sorted(call_ids)
    ]

    output = {
        "schema_version": 1,
        "data_origin": "SYNTHETIC",
        "generation_policy": "BERTH_INDEPENDENT_SYNTHETIC_V1",
        "seed": args.seed,
        "profile_count": len(profiles),
        "profiles": profiles,
    }

    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Created: {destination}")
    print(f"Profiles: {len(profiles)}")
    print("All vessel dimensions and cargo values are SYNTHETIC.")


if __name__ == "__main__":
    main()
