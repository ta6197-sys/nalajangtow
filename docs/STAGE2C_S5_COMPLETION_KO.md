# Docktimize V5 Stage 2C — S5 완료 및 최종 정산 감사 명세

## 목표와 근거
최신 GitHub main `aec9dda`에서 Stage 2B 진단 코드는 존재한다. 다만 PR #73에는 60초/120초 각 20회 반복 수치 결과가 포함되지 않았다. Codex는 먼저 `stage2b_report.json`, `stage2b_report.md`가 로컬에 있다면 확인하고 원인 분류를 기록해야 한다. 결과 미확보를 임의로 PASS 처리해서는 안 된다.

이 패키지는 다음 보수적인 관문을 추가한다. 기존 `S5`는 `GATES['S5']=(5,2)`로 2개 창의 요청 완료만 검사한다. **이번 관문은 `5척 모두 ETU 완료 + 모든 계약 SETTLED + 정수 센트 합계 + 정확한 화물량 + 독립 Replay`를 요구**한다.

## 구현 구조

1. 원본 V5 140척·11선석·3탱크·FIX 15를 `preflight_sources()`로 검사. 정책 버전은 `V5_USER_ETU_SWITCHING_20261112_V1` 필수.
2. 동일 범위의 5척 선택: 기선택 ID가 있다면 `--call-ids-file`로 명시. 없으면 기존 `select_scope()` 규칙 이용.
3. 실행 시작 전 창 수를 결정. 기존 `execute_scale(..., call_count=5, windows=N, tie_break_mode='CANONICAL_ETB_BERTH_V1')`를 호출. 기존 최적화 구현을 다시 작성하지 않는다.
4. 저장된 `execution_report.json`·manifest·window checkpoints 구조 검사.
5. 기존 `scripts/v5_full/verify_resume.py`를 별도 프로세스로 실행해 물리·재고·정산·Rolling 상태 재생. 실패하면 완료 PASS 금지.
6. 마지막 24h 커밋 상태의 `completed_rows`, `contract_accounting_state`, `cargo_progress` 및 누적 센트와 보고서를 교차 검사.
7. 새 감사 파일 `stage2c_completion_audit.json` 기록. 기존 파일 덮어쓰기 금지.

## 5척 완료 판정
- `scope_call_ids`는 서로 다른 실제 원본 항차 정확히 5개여야 함.
- 마지막 `next_state.completed_rows`의 `call_id` 집합이 5개와 같고 중복·범위 밖 항차가 없어야 함.
- 모든 ETU는 마지막 `next_state.at` 이전 또는 같은 시각.
- 모든 항차의 `cargo_progress`는 원본 `cargo_volume_bbl`과 정확히 일치, `remaining_bbl=0`.
- 모든 항차 `contract_accounting_state[cid].settlement.status='SETTLED'`, `demurrage_cents`는 음이 아닌 정수.
- `cumulative_demurrage_cents`는 5개 정수 센트의 합과 일치하고 `null`이 아니어야 함.
- `in_progress_contract_accounting`, `previous_schedule`, `carried_transfers`, `unresolved_accounting_call_ids`에 해결되지 않은 값이 없어야 함.
- 해당 실행의 전체 창 `REQUESTED_WINDOWS_VALIDATED` + 원래 독립 `verify_resume`가 `PASS`.

## 미완료 분류
- **S5 두 창만 정상 종료**: `VALIDATED_WINDOWS_SCOPE_INCOMPLETE` 가능. 실행 오류로 오해하지 않음.
- **N개 창 수행 중 실패**: `BLOCKED_*`; 저장된 report/checkpoint/오류 코드 보존.
- **5척 완료이나 비용 `null`**: `CUMULATIVE_COST_UNRESOLVED`로 FAIL, 0센트 치환 금지.
- **계약 지문 불일치 또는 독립 Replay 실패**: 완료 PASS 금지.
- **일광·기상·외부 공정 유량 부족**: 입력/정책 미해결로 차단. 데이터나 정책을 임의 생성하지 않음.

## 중요 실험 통제
- `--windows`를 늘린 실행은 기존 Checkpoint resume가 아니라 **새 run_id의 새 전체 실행**. Rolling manifest의 `windows`가 해시에 포함돼 있으므로 수정을 통한 재개 금지.
- 서로 다른 창 수의 결과는 일정 후보와 최적화 시점도 달라질 수 있음. 통합 비용의 단순 우열 비교 금지.
- 중첩되는 72h 창별 예상 비용은 **합산하면 안 됨**. 마지막 24h 상태에 승계된 계약 정산 금액만 최종 합으로 인정.
- 새 정책 11개 독립 선석, 4-1/4-2, Direction, Switching, ETU, FIX 및 도선 fail-closed 유지.
- CANONICAL 동률 선택은 12개 그룹 이하에만 적용되고 1차·2차 증명이 없으면 폴백할 수 있음. 별도 최적성 증명 없이 전역 최적이라고 주장하지 않음.

## 다음 게이트
S5의 **다섯 척 완료 + 정산 + Replay** PASS 후, Stage 2C 다음 작업으로 S10, S20, R7, R14, FULL140을 단계적으로 확장한다. 단계별 실패 시 입력/정책/후보/solver/물리/재고/정산/상태승계 분류부터 진행.
