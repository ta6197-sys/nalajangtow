# Docktimize BALANCED 2027-01-09 확장 입력 호환성 패키지

검토 기준: GitHub main `b5c861a7b726e4f1e63da106fbbe261a25bee76b` 및 사용자 업로드 JSON 두 개.

## 구성
- `input/v5_balanced_ext_20270109/`: 이름과 내용을 맞춰 **그대로 복사한** JSON 두 개. 내용 바이트는 변경하지 않음.
- `scripts/verify_extension_inputs.py`: 154척, FIX 15, 11선석, 3탱크, 816 유량, 6h 연속성, SHA-256 검증. `--legacy-flow`를 주지 않으면 과거 696구간 동일성은 미검증으로 남음.
- `scripts/apply_extension_profile.py`: 실제 저장소에서 확인한 앵커만 수정하는 fail-closed 패치. 현재 main 코드가 다르면 중단. 별도 브랜치에서 실행.
- `tests/test_v5_balanced_ext_profile.py`: 프로필 라우팅 및 음성 테스트. 전체 데이터 통합 테스트에는 `DOCKTIMIZE_EXT_INPUT_DIR` 지정 필요.
- `CODEX_EN.md`: Codex 영어 개발 지시서.

## 적용 순서
1. 신입력 두 파일을 별도 입력 폴더에 두고 `verify_extension_inputs.py --input-dir <폴더>` 실행.
2. 가능하면 기존 696유량 파일을 `--legacy-flow`로 제공해 과거 구간 동일성 증명.
3. 최신 main에서 새 브랜치를 생성하고 패치스크립트 --check(기본) → --apply. 저장소의 모든 변경사항 검토.
4. 신규 프로필을 선택하는 별도 실행 설정 파일을 생성: 기존 `config/v5_balanced_execution.json`을 복사하고 `scenario`를 `V5_BALANCED_154_EXT_20270109`, `data_dir`를 새 폴더로 변경.
5. `python -m src.daily_planning.integrated_input.cli --data-dir <폴더> --scenario V5_BALANCED_154_EXT_20270109 --dry-run` 실행.
6. `python scripts/v5_full/run_stage.py --config <새설정> --gate S5 --preflight-only` 실행 후 S5 실제 실행 및 별도 감사. 장기 실행은 정책·도선·일광 커버리지 별도로 확인.
7. 신규 PR을 병합한 뒤 Stage 1 정산 회귀 문제 정리, 이어서 Stage 2A 물리 0~1,000만/운영경고 100만~950만 분리 진행.

## 중요한 구분
- 확장 데이터는 `flow_horizon_end_at=2027-01-10T00:00+09:00`이며 **끝 시각은 배타적**입니다. 2027-01-09 하루의 6h 구간까지 포함됩니다.
- 신규 프로필은 **외부 입력 식별자**입니다. 최적화기의 내부 `scenario_id`는 기존 `V5_BALANCED_154`로 유지해 계산 경로를 재사용합니다. 정규화 스냅샷의 `source_profile_id`로 입력 버전을 구분합니다.
- 원본 소스에 있는 enrichment `external_flow_json_sha256` 값은 과거 696 파일을 가리킵니다. 새 816 파일은 별도 SHA로 검증합니다. 기존 JSON을 몰래 수정해 맞추지 않습니다.
- 고정된 원본 fixture SHA를 사용하므로 파일 내용을 다시 생성·수정하면 새로운 확장 프로필 버전이 필요합니다.
- 출처 해시가 같다고 물리적 재고 적합성 또는 FULL154 전체 완료가 보증되지는 않습니다.
- 검증된 데이터 기간과 선석·도선·일광 운영정책 기간은 독립적으로 검사해야 합니다.
- 이 패키지는 로컬 저장소에 패치를 **적용하기 위한 것**이며, 원격 GitHub는 수정하지 않았습니다. 전체 V5 회귀 테스트는 Codex에서 실행해야 합니다.
# 실행 환경 검증 기록

- 확장 선박/유량 SHA-256: `4d587107a051e83bcc3b9e80e74cbd17585e2d2b555eac1dc5fcb5faae2a7850` / `cabe3a7eab2d1e20333c02eb1d40dfb09b58796d7c4d865eb649994b426fbfe2`.
- 이전 696개 유량의 SHA-256 `9c0d3ee063f54f93af03c122b1d8ee9608ee1a6e6e36c34ba3bdb3589c6ba763`; 별도 원본 파일로 각 탱크의 기존 구간 696개가 확장본과 정확히 일치함을 확인했다.
- 확장 입력은 154척, hard FIX 15건, 11개 선석, 3개 탱크, 탱크당 272개의 연속 6시간 구간이다. 마지막 구간의 종료는 2027-01-10 00:00 KST이다.
- 기존 시험 정책에서 2개 및 3개 창의 입력 범위 사전 점검은 통과했다. 65개 창은 선석 정책의 선언된 기간 부족으로 차단된다. 이는 스케줄 가능성이나 운영 승인이 아니다.
- 패키지의 게이트 실행 스크립트는 경로로 직접 실행할 때 import 오류가 있어 저장소 루트를 import 경로에 추가했다.
- 기존 정산 감사 테스트의 기대값을 PR #78에서 정한 서로 다른 출처 처리에 맞췄다. 정산 공식은 수정하지 않았다.
