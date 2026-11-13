# Stage 1 실행 및 설명자 검증

이 기능은 기존 실행 상태를 유지하면서 `execution_outcome_v1`을 추가한다.
입력 기간이 부족하면 최적화기를 호출하지 않고 `INPUT_COVERAGE_BLOCKED`를 기록한다.
S20은 20척, 3개 창이다. 2026-11-03 00:00 KST 시작 시 필요한 끝은
2026-11-08 00:00 KST이다. 선석 공통 도선창은 선박별 도선 제한을 대체하지 않는다.

실제 BALANCED 합성 입력의 5척, 1개 창 실행 예시 (운영 승인 아님):

```json
{"execution_status":"COMPLETED_REQUESTED_WINDOWS","completion_status":"CALLS_REMAIN","requested_windows":1,"validated_windows":1,"scope_calls":5,"completed_calls":0}
```

체크포인트 하나의 구조 검증 설명자를 별도 폴더에 생성한다:

```bash
python scripts/v5_full/export_simulation_descriptor.py RUN_DIRECTORY 1 OUTPUT_DIRECTORY
```

결과는 `DESCRIPTOR_ONLY_NOT_REPLAY_VALIDATED`이며 독립 시뮬레이션 재생은 `NOT_RUN`이다.
첫 창의 이전 상태는 명시적으로 누락 처리한다. 다른 창은 직전 체크포인트 상태와
지문을 검증한다. 기존 증거를 덮어쓰지 않는다. 겹치는 창의 예상 비용을 더하지 않는다.

Stage 2 전에는 검증된 원본·정책·도선/일출일몰·계약·유량과 이전 상태를 연결하고,
충격 없는 독립 V5 재생의 물리·재고·정산·이월 일치를 별도로 검증해야 한다.
