# Docktimize Step 2 — 선석 매핑·선박 제원 사전검사

## 목표/범위

- 기존 Step 1의 `berth_rules.py`, `config/berth_mapping.json`, `data/sk_facilities.json`을 그대로 사용.
- **추가 파일만 설치.** `models.py`, `context.py`, `exact_search.py`, `optimization.py`, DB, API, 기존 시뮬레이션은 변경하지 않음.
- 이 단계의 `READY`는 **문서 기반 물리적 호환성에 대해 적어도 하나의 PASS 선석이 있다는 뜻**. 운영·안전 승인이나 전체 최적화 제약 충족이 아님.
- 8선석 실체가 확인되지 않았으므로 매핑은 임의로 채우지 않음. Step 1에서 `null`인 상태를 의도적으로 감지.

## 설치 (프로젝트 루트)

```bash
# 변경사항이 없다면 신규 브랜치 생성
git status
git switch main
git pull --ff-only origin main
git switch -c feat/sk-preflight
# ZIP을 프로젝트 루트에 압축 해제한 뒤 아래 테스트 실행
# 1차/2차 테스트를 차례로 실행
python3 -m unittest discover -s tests -p 'test_berth_rules.py' -v
python3 -m unittest discover -s tests -p 'test_sk_preflight.py' -v
```

## 명령어

**매핑만 검증 (현 상태에서는 NOT_READY/exit 2가 정상):**

```bash
python3 scripts/audit_sk_preflight.py
```

**로컬 PostgreSQL에 외부 포트가 공개되지 않은 기본 Docker 설정에서 빈 템플릿 생성 (권장):**

```bash
# 첫 번째 명령은 Docker 내부 DB를 읽기만 하며, 호스트 input/에 ID 목록을 기록
# 1) DB가 가동 중이어야 함
mkdir -p input
docker compose -p solver-daily exec -T web python -c 'import sqlalchemy as sa; from src.daily_planning.database import get_engine; from src.daily_planning.models import port_call; e=get_engine(); c=e.connect(); print("\n".join(c.execute(sa.select(port_call.c.call_id).order_by(port_call.c.call_id)).scalars())); c.close(); e.dispose()' > input/call_ids.txt
# 2) 실제 행 ID가 있는 빈 선박제원 입력 JSON 생성
python3 scripts/create_vessel_profile_template.py --call-ids-file input/call_ids.txt
```

DB에 접속하지 않고 일부 항차만 시험하려면 다음처럼 사용할 수 있습니다.

```bash
python3 scripts/create_vessel_profile_template.py --call-id DEM-0017 --out input/vessel_profiles.local.json
```

로컬 Python에서 PostgreSQL에 접근할 수 있도록 환경을 설정했다면 `--from-db` 옵션도 사용 가능합니다.

`input/`은 현재 GitHub `main`의 `.gitignore`에서 제외되어 있으므로 수동 입력 제원이 의도치 않게 푸시되지 않습니다. 생성 파일이 이미 있으면 덮어쓰지 않고 실패합니다. `--from-db`는 기존 `port_call` ID만 읽으며 DB는 수정하지 않습니다. 로컬 DB가 Docker 내부에서만 접근 가능할 때에는 컨테이너에서 스크립트를 실행하거나 DB 접근 설정을 검토해야 합니다.

`input/vessel_profiles.local.json`에 **근거를 갖춘** DWT/LOA/흘수/화물과 `data_origin`을 입력합니다. 제원 미확인은 `null`을 유지하세요. 예제 파일은 그대로 사용하지 마세요.

**전체 항차의 물리 호환성 사전검사 (매핑 완료 이후 의미 있음):**

```bash
python3 scripts/audit_sk_preflight.py \
  --profiles input/vessel_profiles.local.json \
  --out results/sk_preflight_report.json
```

- exit 0: 8개 고유 선석 매핑 완료 + 감사 대상 항차 모두 하나 이상 물리적으로 PASS
- exit 2: 미완성 매핑 또는 일부 항차의 프로필 누락/선석 PASS 없음
- exit 3: 파일 파싱/입력 오류
- 이 출력을 **CP-SAT 실행 가능 또는 안전 승인으로 해석하지 말 것.** 
- `results/`는 기존 `.gitignore`에 포함.

## 결정해야 하는 내용 (구현자에게 반드시 확인)

1. **`BERTH-01..08`은 실제 물리 선석 8개인가, 대표 그룹인가, 가상 동일 선석인가?** 물리 1:1이 아니면 현재 Step 1의 `validate_mapping`을 그대로 활용해 자동 배정을 해서는 안 됨.
2. **실제 8개 시설 코드**. 엑셀에 있는 22개 프로필 중 8개를 실제 설비 기준으로 선택. 추정 금지.
3. **기존 140항차의 선박 제원 출처**. 실제 자료가 없다면 별도 시나리오 제원 생성 여부/방법 합의. `null`을 임의 숫자로 보정 금지.
4. **`SK#7`/`SK#8` 원문 `석유저제품`의 해석**. Step 1은 자동 오기 보정을 하지 않고 `UNKNOWN` 유지.
5. **`UNKNOWN`을 최적화에서 거절할지, 실험 모드에서 경고와 함께 제한적으로 허용할지**. 안전/운영 검증 없이 적합한 선석으로 표시하지 말 것.

## Step 3 설계 전 조건

- 매핑 8개 전부 선택, 누락/중복 없음.
- 각 항차에 `call_id -> vessel_id/항차별 흘수/DWT/LOA/화물`의 적어도 필요한 부분이 존재.
- Snapshot에 선박/선석 제원·규칙 버전·JSON SHA-256을 포함하도록 설계.
- `build_domain()`에서 미호환 선석 옵션 제거. **DEFER(72시간 후 이월) 옵션만으로 생성된 결과를 정상 최적해로 표시하지 않도록** 평가 및 상태 API를 보완.
- 확정된 과거 결과는 재현성을 위해 이전 정책으로 유지하고, SK 제한조건은 새 버전 정책에서만 적용.
