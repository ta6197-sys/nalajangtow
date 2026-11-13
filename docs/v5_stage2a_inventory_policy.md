# BALANCED 물리 재고와 운영 경고

기존 실행 설정과 체크포인트는 기존 10–95% 하드 제한을 유지한다. 새 실행에서만
`inventory_policy_version`을 `V5_PHYSICAL_BOUNDS_OPERATING_WARNINGS_V1`로 명시하면
BALANCED 탱크의 물리 한계 [0, 용량]이 CP-SAT, 독립 재생, 이월 재고에 적용된다.
원본 최소·최대의 10–95%는 별도 `operational_warnings`로 보고하며 실패나 비용에
반영하지 않는다. 경고는 검증된 이벤트 지점의 극값이며 경고 지속시간을 뜻하지 않는다.

원본 JSON과 정규화 스냅샷은 변하지 않는다. 정책은 실행용 복사본에만 적용하고
체크포인트 설정에 버전을 기록한다. 다른 정책으로 과거 체크포인트를 재생할 수 없다.
V5_3TANK 및 정책 미지정 실행은 이전 동작을 유지한다.

검증: `python -m unittest tests.test_v5_operating_inventory_policy -v`. 확장 입력 S5의
소스 지문은 이전 정책 실행과 동일했으며 2개 창 및 독립 재생이 통과했다. 창 종료 시
5척 중 2척 완료로 전체 범위 완료나 실제 운영 승인을 의미하지 않는다.
