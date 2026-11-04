# Docktimize 제약조건 구현 — Step 1 (비침습적 독립 모듈)

> **상태**: SK 시설 수치 정규화 및 선박/선석 *물리 호환성 검사*까지 구현.
> 기존 `models.py`, `context.py`, `exact_search.py`, `compose.yaml`, DB 및 API는 **변경하지 않음**.
> `PASS`는 흘수·DWT·LOA·화물의 검증된 일부 규칙만 통과했다는 의미. 최종 접안 가능·안전 승인이 아님.

## 구성

- `data/sk_facilities.json`: 첨부 엑셀의 **시설별 제원** 시트에서 SK 기준 K~Q열 등의 수치·근거를 추출한 22개 시설 프로필. UPA-only 미매핑 행은 별도 선석으로 생성하지 않음.
- `src/daily_planning/berth_rules.py`: `PASS / FAIL / UNKNOWN` 검증 코드, 모든 미구현 규칙 `not_evaluated`로 명시.
- `config/berth_mapping.json`: 8개 가상 선석 매핑 템플릿. 모두 `null`이고 **직접 정하기 전에는 자동 적용하지 않음**.
- `scripts/check_berth_rules.py`: 터미널 검증 도구.
- `tests/test_berth_rules.py`: DB 없는 unittest 15건.

## 설치 (프로젝트 루트에서)

1. `git status`로 기존 변경 확인. 수정사항이 있으면 먼저 커밋 또는 stash.
2. `git switch main && git pull --ff-only origin main && git switch -c feat/sk-berth-rules`
3. ZIP의 내용을 `nalajangtow/` 프로젝트 루트에 풀어 경로를 합침. **새 파일만 추가됨**.
4. `python3 -m unittest discover -s tests -p 'test_berth_rules.py' -v`
5. `python3 scripts/check_berth_rules.py --list-facilities`
6. `python3 scripts/check_berth_rules.py --facility 'SKB#2' --draft-m 21.22 --dwt 100000 --cargo 원유`
   - 예상: `FAIL / MAX_DRAFT_VIOLATED` (이 예제는 실패가 정상이며 종료 코드 1).
7. `python3 scripts/check_berth_rules.py --facility 'SKB#2' --draft-m 21.21 --dwt 80000 --cargo 원유`
   - 예상: `PASS` (물리 호환성 범위 내).
8. `python3 scripts/check_berth_rules.py --validate-mapping`
   - 매핑 결정 전에는 `MAPPING NOT READY`가 정상. 임의로 채우지 말 것.

## 다음 개발단계 — 사용자 결정 필요

**Step 2:** `BERTH-01..08`과 실제 SK 시설 코드 8개를 1:1 매핑. 8개가 대표 *선석 그룹*이라면 그룹 모델부터 재설계 필요.

**Step 3:** `VESSEL` 및 항차별 실제 입항 흘수·공선/적재·화물 구분 등의 입력 추가. 기존 `engine_calls()`은 `call_id/available_at/processing_minutes/cargo_quantity_kg/contract`만 반환함. 현재 데이터에 없는 제원을 조용히 채우지 말 것.

**Step 4:** 입력 스냅샷 버전화 + `build_domain()`에서 `check_call_against_virtual_berth()` 검사. `PASS`가 아닌 조합은 최적화 선택지에 넣지 않고, `UNKNOWN`은 입력 보완 요구. 이때 DEFER 옵션이 있으므로 **모든 선박/선석 UNKNOWN이어도 solver가 성공하는 문제**를 감지해야 함. `constraints_applied`/`unverified_count`를 결과에 표시해야 함.

**Step 5:** 접안/하역/이안의 별도 시간·기상 게이트. 기존 `DAYLIGHT_WORK_V2`를 즉시 덮어쓰지 말고 새 `berthing_policy` 버전으로 도입해 이전 결과 재현성을 유지. CP-SAT의 점유구간은 `berthing_start_at`부터 `berth_release_at`까지.

**Step 6:** 조석/UKC·인접선석·예선 등 사용자 엑셀 M03~M12를 확인 가능한 입력 순서대로 추가. 탱크/배관/출고량 미보유는 `NOT_EVALUATED`로 고지.

**Step 7:** Alembic `0003` 마이그레이션은 테이블 매핑/컬럼 스키마가 확정된 후 진행. 기존 0001/0002·데이터·확정 기록 보존.

**Step 8:** API 조회(`GET /api/v1/berths`, `GET /api/v1/constraint-checks`)부터 추가. 최적화 실행/확정 API는 입력 검증과 회귀시험 통과 후 연결.

## 주의사항

- SK 문서간 충돌: 같은 의미·조건에서만 교집합 적용. 비교용 도선사회/UPA 값을 SK 프로필에 중복 입력하지 않음.
- SK#4-1 최소 LOA는 80m/90m 중 **90m**.
- SK#7, #8의 원문 `석유저제품`은 자동으로 `석유정제품`으로 고치지 않음. 해당 화물 조회는 `UNKNOWN` 처리.
- `night_berthing_source`는 참고 원문일 뿐 이 Step의 `PASS`에 들어가지 않음.
- 유효흘수, 조위/UKC, 풍속·파고의 시간 평균·측정 위치, 접안 시간길이, 선석 그룹 매핑, 선박 제원은 추가 확인 필요.
- 현 단계의 시뮬레이션은 실제 항만 운항 승인 시스템이 아님.
