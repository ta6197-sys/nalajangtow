# Docktimize V5 — ETU·Direction·Switching·4-1/4-2·접안 도선 정책 구현 가이드

## 기준과 배포 범위

- GitHub: `ta6197-sys/nalajangtow` / **PR #68가 병합된 `main` 커밋 `9ea1395d6c3c760402aa54acbd41394d302d0fe4`**.
- 사용자 확정 원문: `docs/USER_CONFIRMED_V5_RULES_KO.md`.
- 독립 Python 규칙 모듈: `src/daily_planning/physical_scheduling/v5_user_rules.py`.
- 기존 V3 및 Stage1–4 합성 정책은 하위 호환을 위해 유지하고, **신규 V5 정책 `V5_USER_ETU_SWITCHING_20261112_V1`에서만** 새 조건을 사용한다.
- DB, Alembic, 원본 Excel/JSON, 계약 정산, 탱크 모형은 변경하지 않는다.

## 새 정책의 핵심

1. `ETU = work_end + 7200 seconds = hose_disconnect_end`. 별도의 `unberthing_seconds = 0`, 출항도선 시간 제약 없음.
2. `berthing_seconds = direction_seconds`. 11개 독립 berth_id별 Direction·Switching은 사용자 매핑과 일치. **Direction을 두 번 더하지 않는다.**
3. 일반 동일 선석 A→B: 가장 빠른 B ETB는 `max(ETA_B + Direction_B, ETU_A + Switching_berth)`.
   - 선행 또는 후속 선박이 없으면 Switching 이벤트 없음.
   - B ETA 기준시각이 교대 제약을 이미 넘었다면 Switching에 따른 추가 지연 없음.
   - 현재 후보 한정 CP-SAT에서는 선택한 A/B 일정 쌍을 불허하는 pairwise hard constraint로 적용한다.
4. `BERTH-07/08` 서로 다른 선석의 4-1·4-2 특례:
   - 두 척 모두 GRT≤3000일 때만 두 선석의 동시 점유 허용.
   - 중첩일 경우 **후속 B ETB=선행 A work_end=A ETU−2h** 정확하게.
   - 후속 B 호스 연결 시작은 A ETU 이후, 두 척 동시 하역 금지.
   - 하나라도 GRT>3000이면 중첩 점유 금지. 늦게 온 선박은 일반 순차 접안 가능.
   - 같은 berth_id 내부의 중복 점유는 계속 금지.
5. `BERTH-04/05`가 같은 #1을, `BERTH-03/11`이 같은 #8을 참조하지만 **완전히 독립된 선석**. 참조 이름만으로 공유설비/충돌 추가 금지.
6. GRT 30000보다 크고 75000 미만인 선박의 `02:00≤ETB<08:00` 접안을 거절한다. 일몰 Last POB 제한도 별도로 적용한다.
7. GRT **정확히 30000·50000**, 75000 이상, 저GRT 수입선·흘수≥10.5m 등의 자료만으로 결합식이 확정되지 않은 경우 **`UNSUPPORTED_OPERATIONAL_POLICY`**로 실패 처리한다. 명세서의 미확정 원칙을 따른다.

## 정책 필드와 원본 데이터 관계

새 정책 생성기:

```bash
python scripts/v5_policy/build_user_policy.py \
  --base input/v5_physical_policy.json \
  --output input/v5_user_policy_20261112.json \
  --source-reference USER_CONFIRMED_V5_RULES_20261112
```

기존 정책에 이미 명시된 `hose_connection_seconds`, 작업 가능시간, 예선 수요, 작업시간 모델, 데이터 출처는 그대로 유지한다. **호스 연결 시간이 2시간이라고 새로 확정된 것은 아니다.**

기존 선석 원본의 `hose_disconnect_min` 값이 120분과 다르더라도 원본을 변경하지 않는다. 사용자 확정 정책은 새로운 `policy.hose_disconnect_seconds=7200`으로 기존 원본의 합성 분리시간을 **명시적으로 덮어쓰는 모델 가정**이다. 이 차이는 검증 보고서에 반드시 기록한다.

`switching_start_at / switching_end_at` 필드는 과거 Rolling 체크포인트 스키마와의 하위 호환 때문에 V5 신규 일정에 **동일한 ETU 시각으로 남아 있다(길이 0인 placeholder)**. 이는 수행된 Switching 이벤트가 아니며, 그 값으로 선박 완료를 판정하지 않는다. 조건부 Switching은 선후행 선박 쌍의 ETB 허용 조건으로만 적용된다.

## 출력과 검증

- 새 일정 행에는 `phase_policy_version`, `direction_seconds`, `switching_rule_seconds` 메타데이터가 포함된다.
- `v5_run_review.window_view()`에서 위 메타데이터가 보존돼야 한다.
- 기존 웹은 해당 정책 버전 결과에만 ETU 시점 마커와 실제 후속 교대 제약 막대를 표시한다. **별도 이안 Duration 막대 없음**.
- 24시간 Rolling에서 선박 완료는 ETU 기준. 계약·재고·전수검증은 기존 로직을 유지한다.
- Checkpoint의 정책 지문이 바뀌므로 이전 정책에서 생성된 체크포인트는 새 정책으로 재개하지 않는다. **새 실행 ID/출력 폴더로 재실행**.
- 창별 예상 체선료를 겹쳐 합산하지 않는다. `OPTIMAL`은 유한 후보 집합 범위에만 적용한다.

## 필수 검증 순서

1. `python -m unittest tests.test_v5_user_rules -v` (순수 규칙)
2. `python -m unittest tests.test_v5_user_policy_integrated -v` (실제 Phase·후보충돌 필터)
3. `node --test tests/web-v5-user-rules.test.cjs` (UI 모델)
4. `python -m unittest tests.test_v5_physical_integration tests.test_v5_cost_aware_candidates -v`
5. `python -m unittest tests.test_v5_integrated_rolling tests.test_v5_rolling -v`
6. 전체 Python 회귀 및 기존 JS 테스트, 실제 verified V5 입력의 작은 범위 실행과 독립 재생 확인.

**중요:** 사용자 제공 140항차 전체 원본과 실제 로컬 PostgreSQL/Docker/OR-Tools는 이 코드 패키지 내에 없으므로, 최종 실제 입력 통합 실행 여부는 Codex가 실행 환경에서 입증해야 한다. 새 정책 때문에 source policy 불일치가 나타나면 원본 수치를 조작하지 말고 원인과 출처를 보고한다.

## 위험 및 남은 항목

- 특수 GRT·흘수·Last POB의 전체 Boolean 결합식은 이미지 출처만으로 확인되지 않았다. 임의 승인 금지.
- 4-1·4-2의 두 GRT≤3000 선박 중첩시, B의 ETB 후보 `A.work_end`가 탐색 집합에 반드시 포함되는지 CP-SAT 결과로 점검. 시간이 부족해 후보가 미생성될 경우 이를 `INFEASIBLE`이라고 단정하지 말 것.
- Source workbook에 4-1/4-2 외 추가 명시적 그룹 제약이 있으면 그대로 유지. #1/#8 복제 이름만으로 신규 그룹을 만들지 말 것.
- 순수 단위 테스트와 전체 V5 실증 테스트를 구별할 것. PR 및 UI에서 검증 범위를 과장하지 말 것.
