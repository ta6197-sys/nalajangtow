# 로컬 웹 실험

```bash
cd requirement
docker compose -p solver-daily up -d --build web
```

브라우저에서 **http://127.0.0.1:8765**를 연다. 웹은 로컬 PC에만 열리고, `web-postgres` 전용 DB를 사용한다. 기존 140항차 DB와 결과는 변경하지 않는다.

기존 야간 접안 허용 실험 24항차의 결과는 같은 웹 DB의 `web-scenario` 실험 키로 보존한다. 현재 기본 화면은 `web-daylight-v1` 키로 같은 입력 24항차를 2026-11-03부터 새 일출·일몰 정책으로 다시 운영한다.

1. 웹의 **XLSX 템플릿 다운로드**를 누른다. `contracts`와 `calls` 두 시트의 열 이름을 유지하고 2행부터 데이터를 채운다. 계약·항차는 각각 1행 이상 필요하다.
2. 완성한 파일을 업로드한다. 모든 행을 검증한 뒤 한 번에 저장하며, 원본과 SHA-256 해시를 보존한다. 시각은 KST다. `known_at`을 비워두면 NOR 날짜 00:00에 공개한다는 가상 규칙을 사용한다.
3. **현재 날짜 후보 계산**을 누른다. 72시간 시작 범위의 A/B/C를 각각 최대 180초 탐색하고, 모든 대상 항차의 완료까지 공통 FCFS 이월 규칙으로 비용을 평가한다.
4. 후보 카드에서 선석·시작·완료 일정을 확인한다. 아래 비교표에는 A 대비 B/C의 정확 평가 체선료, 대기시간, 선석 변경, 시작 이동과 항차별 차이를 표시한다. 서로 다른 실행 가능 후보가 없으면 해당 칸은 비어 있다.
5. 하나를 누르면 당일 00:00 일정이 확정된다. 이때부터 당일 확정 작업은 수정할 수 없다.
6. **확정된 일정으로 당일 운영·다음 날 후보 계산**을 누르면 24시간을 가상 운영한 뒤 다음 날짜 A/B/C를 자동 계산한다. 매일 적용 후보는 사용자가 직접 고른다. 지난 날짜 결과는 아래 표에 남는다.

`contracts` 시트에는 `contract_id`, `allowed_minutes`, `daily_demurrage_usd`, `notice_hours`, `nor_valid`(Y), `early_start_if_work`(Y/N), `day_mode`(SHINC/SHEX_SUN/SHEX_WEEKEND/SHEX_SAT12), `weather_policy`(COUNT/EXCLUDE/HALF), `post_demurrage_exceptions`(NONE/OWNER_ONLY/OWNER_AND_TERMINAL), `despatch_policy`(NONE/HALF_WORKING_TIME), `holidays`(선택, `YYYY-MM-DD`를 쉼표로 구분)을 입력한다. `calls` 시트에는 `call_id`, `contract_id`, `nor_at`, `available_at`, `cargo_quantity_kg`, `work_minutes`, `known_at`(선택)을 입력한다. 시각은 `2026-11-03 19:40`처럼 분까지 적는다. 기존 6열 XLSX도 업로드할 수 있다.

업로드 뒤 미착수·미확정 항차는 웹에서 수정·취소할 수 있다. 변경 전후 값과 사유는 `operation_event` 테이블에 남고, 변경 전 후보는 `SUPERSEDED`로 보존한 다음 새 후보를 계산한다. 취소된 항차는 이후 계획에서 제외된다. 실험용 시나리오에는 SK CLX 내부 확정 기준 자료가 없으므로, 00:00 확정은 이 웹의 명시적 운영 가정이다.

현재 입력을 한꺼번에 수정하려면 **현재 입력 XLSX 다운로드·수정**으로 파일을 받은 뒤 다시 올려 **변경 미리보기 → 이 변경 적용**을 누른다. 파일에서 빠진 항차는 유지하며 취소는 항차별 취소 버튼을 쓴다. 기존 계약은 고정이고 새 계약 추가는 가능하다. 확정·진행·완료 항차는 변경할 수 없다. 기존 항차의 `known_at`은 유지한다. 미리보기는 DB를 바꾸지 않는다.

후보 체선료는 항차별 계약의 NOR·통지기간에서 시작하는 계약 시계에 계약별 휴일·기상 제외 규칙을 적용해 완료까지 정산한다. 총 대기는 모든 평가 대상 항차의 작업 가능 시각부터 작업 시작까지의 합계로, 이전 날짜의 확정 항차도 포함한다. A는 정확 평가 체선료를 먼저 최소화하고 같은 비용에서는 대기를 줄인다. `OPTIMAL`은 생성된 시작시각·선석 선택지 안에서의 증명이며 연속된 모든 분 단위 일정의 전역 최적성 증명은 아니다.

기상은 `input/weather_for_optimizer_30min.csv`의 **과거 관측 이동 시나리오**를 사용한다. 새 일정의 접안·하역 시작은 기상이 허용하는 일출 이후부터 일몰 전까지만 배정한다. 하역이 시작된 뒤에는 야간에도 진행한다. 일출·일몰은 `data/울산_기상통합데이터.csv`의 계획일 공식값을 추출한 `src/daily_planning/daylight_windows.csv`(2026-11-01~2027-03-31)를 사용한다. 이 파일의 해시는 계획 입력 스냅샷에 보존한다. 기존 확정 일정과 과거 실행은 그대로 재현한다. 선석은 기존 승인대로 동일한 가상 선석 8개다.

결과 파일은 `results/web/{run_id}/`에 저장된다. DB와 결과는 컨테이너를 다시 시작해도 유지된다. 종료는 `docker compose -p solver-daily stop web web-postgres`로 한다.
