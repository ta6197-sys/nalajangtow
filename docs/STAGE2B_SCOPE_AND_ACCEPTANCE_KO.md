# Docktimize V5 — Stage 2B: 전체 적응형 탐색의 재현성·시간 예산 감사

## 1. 기준 및 판단

- GitHub: `ta6197-sys/nalajangtow`, `main` `164cb41a173d9317d64a9740b3fc4de9aa324b57` (PR #72 병합).
- PR #72: 고정 후보 영역의 정규 일정 선택 `CANONICAL_ETB_BERTH_V1` 구현; 12개 결정 그룹까지 지원. 병합 PR 제목은 실제 V5 원본 고정 후보 실험에서 동일 일정이 선택됐다고 보고.
- **원본 고정 후보 재현성 ≠ 전체 적응형 후보 확장 및 Rolling 재현성.** 실제 PR #72 반복 실행 원자료는 레포에 저장되지 않았으므로 수치 변동 폭을 임의 인용하지 않음.
- Python 3.11과 Docker 환경 변경은 사용자 완료: 재실행환경 변경 작업 제외.

## 2. 이번 PR 목적

기존 최적화 코드를 **바꾸지 않고**, 실제 V5 입력 140항차·11선석·3탱크 중 **동일 S5 5항차/2창**을 기준으로 `60s`·`120s` 창별 시간 예산의 전체 적응형 비용 최적화를 각 20회 독립 실행한다.

1. 각 반복 실행은 새로운 run ID와 결과 폴더를 사용하여 S5 두 계획창을 생성한다.
2. 선택한 S5 항차 5건은 별도 `scope_call_ids.json`으로 모든 반복 실행에서 고정한다.
3. 입력/정책/도선/일광/계약 맥락·코드 버전·Python/OR-Tools·도구 설정을 study manifest에 기록한다.
4. 원래 `run_stage.py`를 호출하여 `CANONICAL_ETB_BERTH_V1` 동률 해소 모드를 활성화한다.
5. 모든 성공 실행에서 원래 `verify_resume.py`를 별도 호출해 전체 2창의 독립 물리·재고·정산·승계 재생을 확인한다.
6. 유효 실행의 초기/최종 후보 집합 지문, CP-SAT 최적성, 정확한 체선료(센트), ETB 최대 편차, 선석 변경 여부를 분석한다.
7. 60초 vs 120초에서는 **동일 반복 인덱스의 검증된 결과**만 비교한다.

## 3. 실행 명령

레포 루트에서, 사용자 로컬 V5 실행 설정 파일을 그대로 활용한다:

```bash
python -m unittest tests.test_v5_budget_evidence -v

python scripts/v5_stability/stage2b_budget_audit.py \
  --mode run \
  --config /path/to/verified/execution-user-rules.json \
  --study-root results/v5_stage2b/s5_budget_60_120_run01 \
  --budget 60 --budget 120 --repetitions 20 \
  --tie-break-mode CANONICAL_ETB_BERTH_V1 \
  --candidates-per-pair 6 --total-candidates 240 --improvement-rounds 2

python scripts/v5_stability/stage2b_budget_audit.py \
  --mode analyze \
  --study-root results/v5_stage2b/s5_budget_60_120_run01
```

출력 루트는 **새 폴더**여야 한다. 이전 실험 덮어쓰기 금지. 중단되면 남아 있는 부분 시험 증거는 유지하고 분석할 수 있으나 `PARTIAL_DIAGNOSTIC`으로 취급한다.

## 4. 결과 파일

- `study_meta.json`: 원본·정책·daylight·계약 맥락 지문, Git SHA, Python/OR-Tools 버전, 범위, 후보/시간 예산
- `scope_call_ids.json`: 5항차 고정 범위
- `trials/budget_N/repeat_XXX/runs/<run-id>/`: 기존 Stage-4 S5 Checkpoint, manifest, 실행 보고서
- `trials/budget_N/repeat_XXX/trial_record.json`: subprocess 상태, 구조 검증, 독립 Replay 상태, 오류
- `stage2b_report.json`: 각 예산의 반복성 및 서로 다른 예산 간 차이
- `stage2b_report.md`: 사람이 읽을 수 있는 한글 요약

## 5. 해석과 다음 변경 기준

| 관측 결과 | 권고 작업 |
|---|---|
| A: 초기 후보 지문 변동 | 후보 생성의 공유 실시간 예산/순서/자료 일관성 조사 |
| B: 초기 지문 같음, 최종 지문 다름 | 비용 기반 후보 확장과 종료 기준 수정 검토 |
| C: 최종 후보 같음, 센트 비용 다름 | CP-SAT 중단 상태, 최적성, 시간 예산 검증 |
| D: 후보·비용 같음, ETB/선석 다름 | Stable tie-break 실행 여부/증명/폴백 확인 |
| E: 두 예산 모두 20/20 동일 | 무의미한 알고리즘 변경 없이 Stage 2C 전체 완료 게이트 진행 |

주의: 정규 동률 해소는 최대 12 결정 그룹에서만 실행한다. 계획창에 더 많은 선박이 선택되면 `SKIPPED_SCOPE_OVER_12_CALLS`는 오류가 아니라 **별도로 보고할 한계**다. S5 시험만으로 140척 안정성 증명 불가.

## 6. 성공 기준

- 60초 20회 및 120초 20회 **각각** 요청한 2창 `REQUESTED_WINDOWS_VALIDATED`.
- 모든 실행의 독립 Replay `PASS` 및 구조 감사 통과, 입력·정책·5항차 범위 동일.
- 수치·상태가 달라지면 반드시 진단 결과에 기록; 예산이 높아도 반드시 더 낮은 비용이라고 단정하지 않음(최종 후보 영역이 다를 수 있음).
- 서로 겹치는 72h 계획창의 예상 체선료를 누적 체선료로 합산하지 않음.
- 5항차 중 2창 뒤 완료한 척수와 전체 5척 완료를 구분.
- 실제 140항차 전체 최적성, 현장 운영 승인, 모든 계약 정산 완료라고 주장하지 않음.

## 7. 비범위

- 엔진 목적함수, 후보 생성, 시간 제한, 선석 규칙, 계약 비용, 재고, Docker, Alembic, DB, 웹 수정 **금지**.
- 실험에서 확인된 문제를 개선하는 코드 변경은 **증거를 확인한 뒤 별도 후속 PR**로 분리한다.
- Stage 2C S5 전체 완료/R7/R14/FULL140과 Stage 3 DB·웹 연결은 이 PR에서 수행하지 않음.
