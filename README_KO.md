# Docktimize Risk & Trade-off — 단계별 코드 스켈레톤 v1

GitHub 기준: `ta6197-sys/nalajangtow`의 기존 Python/SQLAlchemy/CP-SAT + `BaseHTTPRequestHandler` 웹.
**부이(SBM)는 사용하지 않는다. 현재 일반 물리 선석 8개 모델을 유지한다.**
기존 파일을 덮어쓰거나 DB를 수정하는 코드는 이 ZIP에 포함하지 않았다.

## 구현 상태 (중요)

| 단계 | 모듈 | 실제 상태 |
|---|---|---|
| 1a | `candidate_reader.py` / `tradeoff.py` | **구현됨**: 기존 PostgreSQL A/B/C 조회 + 정확 평가값의 비교 |
| 1b | `scenarios.py` | **구현됨**: NumPy 기반 합성 시나리오, 동일 시드 재현 |
| 1c | `metrics.py` | **구현됨**: 기대값, 가중 VaR·CVaR, 후보 간 공통 시나리오 확률 |
| 1d | `service.py` / `cli.py` | **구현됨**: 후보 읽기, 비교, 시나리오 준비; 비용 시뮬레이션은 없음 |
| 2 | `replay.py` | **미구현 인터페이스**: 반드시 실제 작업·기상·계약 정산 연결 필요 |
| 2 | `api_adapter.py` / `web/risk_charts.js` | **부분 구현**: 조회 함수, ECharts 렌더러. 실서비스 라우트/HTML 배선은 없음 |
| 3 | `sensitivity.py` | **선택 모듈**: SALib용 Morris/Sobol 래퍼 완성, 실제 리플레이 함수 연결 필요 |
| 4 | `extensions.py` | **인터페이스만**: EMA/MAPS 호출 미구현 |

**리스크 지표는 재생된 시나리오별 실제 체선료가 들어온 뒤에만 생성한다.**
합성 충격만 생성한 상태에서 VaR·CVaR을 꾸며 출력하는 코드는 없다.

## 설치/덮어쓰기 방지

1. GitHub에서 PR #6 → PR #7 병합 및 회귀 테스트가 완료되었는지 먼저 확인한다. 2026-11-05 확인 기준 둘 다 미병합.
2. 새 브랜치 `feat/risk-tradeoff-engine-v1` 생성.
3. 본 ZIP의 `src/daily_planning/risk/`를 같은 경로에 **추가**한다. 루트의 `config/risk_synthetic_v1.json`, `tests/test_risk_*.py`와 필요 시 `web/risk_charts.js`를 추가한다.
4. 기존 `src/daily_planning/evaluation.py`, `contracts.py`, `optimization.py`, DB 테이블, `web_app.py`는 이 단계에서 수정하지 않는다.
5. 1차 검증은 `python -m unittest discover -s tests -p 'test_risk_*.py' -v`.

> 이미 동일 이름의 파일이 있다면 먼저 diff 확인; overwrite 금지.

## 실행 명령

```bash
# 기존 가상환경에서 NumPy 설치 (서버 실행에는 별도로 기존 requirements-db.txt 필요)
python -m pip install -r requirements-risk.txt
python -m unittest discover -s tests -p 'test_risk_*.py' -v

# 실제 PostgreSQL에 저장된 최적화 run_id 확인 후 (읽기 전용)
python -m src.daily_planning.risk.cli \
  --run-id '<YOUR_EXISTING_RUN_UUID>' \
  --config config/risk_synthetic_v1.json \
  --out results/risk-stage1/<NEW_OUTPUT_DIR>

# 데모는 산술 검증용 가상 비용이며 실제 항만 결과가 아님
python examples/standalone_demo.py
```

CLI는 `tradeoff.json`과 `scenario_manifest.json`을 생성한다. `--save-all-scenarios`를 붙이면 전체 합성 충격도 별도 JSON으로 기록한다. 기존 파일이 있으면 덮어쓰지 않고 실패한다.

## 기존 코드의 정확한 연결점

- `schedule_candidate.evaluation_metrics.evaluations[]`에서 `comparison_evaluation_id`인 BASELINE 결과 조회
- `evaluation.summary.demurrage_increment_to_completion_cents`와 `vessel_results[*]` 사용
- `planning_run.run_config.input_snapshot.snapshot_hash`로 동일 입력 보장
- `schedule_candidate.candidate_rank` 1=A, 2=B, 3=C
- A가 `FEASIBLE`이면 최저 비용임을 보장할 수 없으므로 실제 관측 최저 후보를 별도 표시
- 기존 비용은 `contracts.py` 계산, 원 단위 `cents`; 공통 시간 기준은 기존 평가 결과

## 다음 단계별 Codex 최소 작업 지시 (이 문장만 복사해서 단계별로 사용)

**2-1 리플레이:** `risk/replay.py`의 `evaluate_scenario()`만 실제 `evaluation.py`, `rolling_horizon.py`, `weather_optimizer.py`, `contracts.py`, PR #7의 `berth_phases.py`와 연결하라. 정상 0충격 시 `evaluate_baseline()`과 1센트까지 일치해야 한다. 날씨 중단, 작업량 보존, [berthing_start,berth_release), 24h Freeze, FCFS tail까지 평가하라. 기존 DB 원본을 수정하지 마라.

**2-2 DB/API:** 검증된 재생 결과만 저장하는 Alembic 추가 테이블 (`risk_analysis`,`risk_scenario`,`risk_result`)을 설계하고 `api_adapter.py`를 `web_app.py` GET 라우트에 연결하라. DB 기존 테이블은 보존하라. OpenAPI와 테스트 수정. 실패 시나리오는 CVaR 입력에 몰래 제외하지 마라.

**2-3 UI:** 로컬 배포본 ECharts를 추가하고 `risk_charts.js`의 세 렌더 함수를 기존 `web/app.js` 후보 비교 탭에 연결하라. Python 결과의 수치를 그대로 표시하라.

**3 민감도:** 기존 재생을 `sensitivity.py`의 evaluator로 연결. Morris 먼저, Sobol 나중. Sobol은 독립 균등 입력 가정과 별도 설계 표본 사용. 실제 확률이라는 주장 금지.

**4 확장:** `extensions.py`를 EMA Workbench의 Policy/Scenario나 MAPS식 자원 모델에 필요한 경우에만 구현. 현재 계약 정산 엔진을 대체하지 마라.

## 실제 시스템 테스트에서 반드시 확인할 것

- 실제 A/B/C가 동일한 run_id/입력 snapshot 인지
- 선박별 금액 합계 == 후보 금액 합계
- `NO_SHOCK` 재생 결과 == 기존 BASELINE (원 단위 cent 정확히)
- 세 후보에 정확히 같은 외생 충격 시나리오를 적용했는지
- 시나리오 지연 전파 후 선석 이중 점유가 없는지
- 추가 작업시간이 물량 kg를 변경하지 않는지
- 야간 중단과 기상 중단을 구별하고 계약 예외 산입 여부가 변하지 않았는지
- V3 물리 제약을 통과하지 못한 33항차를 강제로 통과시키지 않았는지
- 리스크 분석이 `schedule_commit`, `call_state`, `operation_event`를 변경하지 않았는지
- 미검증 기상·도착 확률은 `SYNTHETIC_ASSUMPTION`으로 표시하는지

현재 ZIP은 기존 PostgreSQL/Docker 실행 환경과 연결한 통합 검증을 거치지 않았다. 별도 Python 유닛 테스트 결과만 신뢰할 것.

추가로 `persistence.py`는 Alembic 마이그레이션 이전까지 **명시적으로 NotImplementedError**를 발생시키는 DB 저장 경계다. 샘플 데이터를 운영 DB에 넣는 코드는 포함되지 않는다.
