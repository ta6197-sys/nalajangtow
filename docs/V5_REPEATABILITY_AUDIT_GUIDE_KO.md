# Stage 1 — 동일 입력 반복 실행 감사 사용 설명서

## 전제

- `main` PR #70 이후의 최신 코드를 작업 브랜치에 반영한다.
- Python 3.11과 기존 프로젝트 의존성은 사용자가 이미 검증했다. **별도의 환경 재검증 PR은 만들지 않는다.**
- 검증 가능한 **실제 V5 원본 4개 파일**과 신규 정책 `V5_USER_ETU_SWITCHING_20261112_V1`, 명시적 daylight·pilotage, pricing/clock 입력으로 실행 설정을 준비한다.
- 이 작업은 PostgreSQL/웹/Dockerfile/최적화 로직을 수정하지 않는다. 로컬 `results` 아래 별도의 출력 폴더만 새로 만든다.
- 실제 원본 계약 자료는 ZIP에 없으므로 본 패키지는 로컬 원본 기반 20회 실행을 여기서 수행하지 못했다.

## 레포 루트에서 실행

### 0. 단위 테스트

```bash
python -m unittest tests.test_v5_repeatability -v
```

### 1. C — 전체 Rolling 20회 (새 결과 경로 필수)

```bash
python scripts/v5_stability/repeatability_audit.py \
  --mode run \
  --config input/v5_execution.json \
  --study-root results/v5_repeatability/study_s5_20261112_01 \
  --repetitions 20 \
  --seconds-per-window 60 \
  --candidates-per-pair 6 \
  --total-candidates 240 \
  --improvement-rounds 2
```

- `study-root`는 존재하지 않는 새 폴더여야 한다. 기존 실행결과 덮어쓰기 금지.
- 반복마다 별도 Python 프로세스로 원본 `scripts/v5_full/run_stage.py --gate S5`를 실행한다.
- 성공한 2창 실행마다 `scripts/v5_full/verify_resume.py`의 독립 Replay가 PASS인지 기록한다.
- C가 실패해도 실행별 실패/원인 증거를 저장하고 A/B 및 `analyze`는 가능한 범위에서 계속 수행한다.
- 기존에 임시 폴더에서 나온 S5 결과는 **이번 20회 표본의 일부가 아니다**. 새 폴더·동일 설정에서 독립 실행한다.

### 2. A — 초기 ETB 후보 생성 20회

```bash
python scripts/v5_stability/repeatability_audit.py \
  --mode initial --config input/v5_execution.json \
  --study-root results/v5_repeatability/study_s5_20261112_01 \
  --seconds-per-window 60
```

- 독립 재생 PASS인 C 실행의 첫 창에 등장한 정확한 항차 ID를 사용한다.
- CandidatePool.initial()만 사용한다. CP-SAT/비용 기반 확장은 호출하지 않는다.
- C 20회 중 한 번도 독립 Replay PASS가 없으면 A/B는 실행하지 않고 `BLOCKED` 처리한다.

### 3. B — 고정 후보 도메인 CP-SAT 20회

```bash
python scripts/v5_stability/repeatability_audit.py \
  --mode frozen --config input/v5_execution.json \
  --study-root results/v5_repeatability/study_s5_20261112_01 \
  --seconds-per-window 60
```

- 성공한 C 실행의 1번 창에서 `optimizer_result.candidates_by_call_and_berth`를 불러와 해시와 함께 영구 저장.
- 모든 B 반복은 **동일한 후보 집합**, 동일한 원본과 정책, 체선료 목적함수, CP-SAT+Exact Inventory, 동일 첫 창에서 실행.
- `candidate_improvement_mode=NONE`은 적응형 확장을 끄기 위한 **진단 전용 설정**. 1차 체선료 최소화와 탱크 모델은 변경하지 않는다.
- B에서 원본 5척의 다른 계획창을 재현했다고 주장하면 안 된다. **첫 계획창의 고정 도메인 실험**이다.

### 4. 분석 결과 생성

```bash
python scripts/v5_stability/repeatability_audit.py \
  --mode analyze \
  --study-root results/v5_repeatability/study_s5_20261112_01
```

결과:

- `study_meta.json`: Git SHA, Python/OR-Tools, 원본/정책/도선/일광 지문, S5 범위/제한.
- `runs/<run-id>/`: 각 S5의 원본 Checkpoint, execution_report; 덮어쓰기 없음.
- `records/run_*.json`: 각 subprocess 종료코드, 실행시간, 독립 Replay 상태/진단.
- `initial/initial_*.json`: A 초기 후보 해시/개수.
- `frozen_domain.json`, `frozen/frozen_*.json`: B 동일 도메인과 각 정수 센트·ETB 결과.
- `repeatability_report.json`: A/B/C 요약, 창별 원인 플래그, 선박별 ETB 차이와 선석 변경.
- `repeatability_etb_rows.csv`: 항차·계획창·선석·UTC ETB·정수 센트·도메인 해시.
- `repeatability_report.md`: 요약 브리핑.

## 해석 순서

1. C의 20회 실행과 독립 Replay가 모두 성공했는지 확인. 미성공이면 일정 안정성 정상 판정 금지.
2. A 초기 후보 도메인이 20회 동일한지 확인.
3. B 고정 후보 영역의 비용·선석·ETB가 20회 동일한지, CP-SAT OPTIMAL 증명 여부 확인.
4. C 초기 도메인 동일하지만 최종 도메인이 다르다면 적응형 확장/시간 예산 영향 조사.
5. B와 C에서 최종 후보·비용은 같지만 ETB가 다르면 동률 해소 문제를 가설로 두되 **모든 최종 도메인의 OPTIMAL 증명이 없다면 '동등 최적해'라고 단정하지 말 것**.
6. 모든 지문이 같고 ETB·선석·비용도 동일하면 이 S5 설정의 재현성이 양호하다. 140항차나 다른 CPU/시간 예산의 재현성을 의미하지는 않는다.

## 시간 예산 민감도

시간 제한 60초 대 120초 등 비교를 원하면 **별도의 새 `study-root`**에서 같은 `--call-ids-file`, 같은 코드/원본/정책과 다른 `--seconds-per-window`로 C/A/B를 실행한다. 다른 예산 연구를 원래 연구 폴더에 섞지 않는다. 두 JSON 보고서의 창별 비용·후보·ETB·실행시간을 비교하여 다음 단계의 개선 대상을 정한다.

## 실패 차단 및 누적 비용

- 예외·타임아웃·정산 미결·미확정 도선 조건을 PASS로 보정하지 않는다.
- S5 2창 검증과 전체 5척의 완료를 구분한다.
- 서로 중첩되는 계획창 체선료를 합산하여 누적 체선료로 사용하지 않는다.
- 별도 승인 전에는 결과를 실무 운영 확정 스케줄이나 140항차 전체 최적해로 표시하지 않는다.
