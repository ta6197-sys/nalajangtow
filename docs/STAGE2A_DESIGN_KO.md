# Docktimize V5 — 2단계 A: 동일 비용 해의 안정적인 선택

## 최신 확인 기준

- GitHub `ta6197-sys/nalajangtow` `main` SHA `82df95d95ec4e68f0dcfd27af2c08b9a867107f2` (PR #71 병합).
- PR #71 제목: S5 재현성 감사 완료, **고정 후보 재풀이 동일 비용의 다른 일정을 선택한 사례 수정 필요**.
- PR에 정량 보고서(JSON, ETB 편차, 20회 횟수)는 저장되지 않아 실제 최대 변동폭은 확인되지 않음.
- Docker Python 3.11 환경 재검증은 사용자 완료로 간주하며 반복하지 않음.

## 확정된 3단계 계획에서의 위치

1. **1단계 완료(도구 병합)**: V5 반복 실행 감사 A/B/C. 실제 결론의 상세 지표는 로컬 산출물에서 확인 필요.
2. **2단계 진행(이번 2A)**: 동일 정수 센트 최적해의 결정적 선택을 추가하고 S5 실증한 후, 관측 문제에 따라 후보 예산·탐색 개선 및 S10/S20/R7/R14/FULL140 확대.
3. **3단계 대기**: 기존 PostgreSQL public 스키마 V5 전환, 데이터셋/실행/후보 영속화, 웹/리스크 통합.

## 변경 원칙

- 기존 CP-SAT 물리/재고/계약 목적을 변경하지 않는다.
- 기존 기본값 `tie_break_mode='NONE'`은 변경하지 않는다.
- 선택 모드 `CANONICAL_ETB_BERTH_V1`에서만 1차 **정수 센트 체선료 최적성 `OPTIMAL`** 증명 후 추가 탐색한다.
- 1차 비용을 정확히 고정하고, `call_id` 순서로 각 선박의 ETB 시각(UTC), `berth_id`, 호스 시작 및 완전한 옵션 데이터를 **사전식**으로 최소화한다.
- 각 2차 탐색에서도 `OPTIMAL`이 증명돼야 그 선택을 인정한다. 미증명/시간초과/대규모 항차는 검증된 1차 incumbent 그대로 반환한다.
- 같은 비용의 비용 기반 확장 결과는 옵션의 안정된 정렬 키로 선택한다. 더 높은 체선료를 안정성을 위해 수용하지 않는다.
- **12개 의사결정 그룹을 넘으면 2차 탐색을 건너뛴다.** 다항차에 대한 설계/성능 검증 없는 과다 CP-SAT 반복 방지. S5/S10만 우선 검증. 140항차 완전 안정화 완료를 주장하지 않는다.
- 기존 선석·ETU·Direction·Switching, #4-1/#4-2, 도선·FIX·Dynamic Lock·Exact Inventory·계약 정산 그대로 유지.
- 새 모드는 `run_stage.py --tie-break-mode CANONICAL_ETB_BERTH_V1`로 opt-in. Checkpoint manifest에 모드가 기록되므로 기존 실행 재개와 섞이지 않는다.
- 승인된 이전 일정에 대한 절대 변경 최소화가 아니라 **최초 실행부터 고정된 표준 동률 해소**이다. 이전 승인 일정과의 근접성 최소화는 별도 정책·승인 일정 입력이 있어야 하므로 이번에 임의 추가하지 않는다.

## 검증 필수 관문

1. Installer `--diff` → 사용자 검토 → `--apply` (Git main 기준, clean tree), 변경 전후 테스트.
2. 신규 단위 테스트, 기존 V5 전체 회귀, PR #71의 20회 B 고정 후보 테스트.
3. 실제 5척·2창 동일 입력의 기존 기본 모드 / 신규 모드를 별도 결과 폴더에 실행하고 원본 기반 `verify_resume.py`로 두 결과 모두 검증.
4. 새 모드 20회 반복: 정수 센트 비용 동일, ETB/선석 동일, 후보 영역 일치, 동률 선택 `PROVEN_CANONICAL_SELECTION` 또는 미증명 명시.
5. 호스 연결 시작이 다른 동등 ETB 옵션도 동일하게 선택되는지 확인. 원본 대비 실무 안정성 개선 여부는 숫자로 입증.
6. 상위 비용이 악화되거나 물리·재고·정산 검증이 실패하면 변경을 완료 처리하지 않음.
7. 기본 `NONE` 경로 회귀 완전 보존 및 Checkpoint 재개 검증.

## 주의: 이번 ZIP의 검증 한계

- 이 환경에는 최신 GitHub 전체 워킹트리와 OR-Tools 패키지 및 140항차 원본이 없으므로 **실제 CP-SAT/전체 소스 통합 실험은 수행하지 못함**.
- Python 표준 라이브러리 기반 선택/순서 및 Installer 테스트만 수행한다.
- 후속 Codex가 검증에 성공하기 전엔 `S5_REPEATABILITY_FIXED`라고 보고하지 않는다.
- `OPTIMAL`은 생성한 유한 후보 집합에만 적용하며 58일 글로벌 최적성을 의미하지 않는다.


## PR72 v2 보완점 및 실제 20회 검증

이전 패키지에는 OR-Tools 테스트 파일이 ZIP에 들어 있었으나 자동 설치 목록에는 없었다. v2에서 설치 목록을 보완했다. 또한 `scripts/v5_stability/stable_frozen_repeat.py`를 새로 제공하여 기존 Stage-1 연구 폴더의 **검증된 첫 번째 Rolling 창의 동일 후보 집합**을 사용해 새로운 동률 해소 모드로 20회 별도 프로세스를 실행할 수 있다.

```bash
python scripts/v5_stability/stable_frozen_repeat.py \
  --config /path/to/existing/v5_execution.json \
  --stage1-study-root /path/to/existing/stage1_study \
  --output-root results/v5_stage2a_frozen20_fresh \
  --repetitions 20 --seconds-per-window 120
```

결과 `repeatability_stage2a_report.json`, `etb_per_vessel.csv`, `trials/trial_*.json`은 오직 **첫 창 고정 후보 집합**에 대한 근거이다. 20회 모두 재현되었더라도 S5 두 계획창 또는 전체 항차 재현성을 의미하지 않는다. `PROVEN_REPEATABLE_IN_FIXED_DOMAIN`은 20회 실행에서 체선료·ETB·선석이 동일하고 각 시행의 1차·2차 최적성 증명이 있을 때만 가능하다. 2차 증명이 없는 동일 일정은 `OBSERVED_REPEATABLE_BUT_CANONICAL_NOT_PROVEN`으로 구분한다.

실제 원본 V5 데이터 기반 실행과 OR-Tools 통합 검증은 사용자의 로컬 Codex 환경에서 수행해야 한다.
