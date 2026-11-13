# W16: 관리자 요청형 영속 shadow 진단 이력

## 범위
이 기능은 자동 확정이 아니다. 명시적인 관리자 요청 때 기존 읽기 전용 preview를 서버에서 다시 계산하고 그 진단 결과를 기록한다. 실제 검토 결과, 사건 상태, 통계, 교육, 방송, 보관 동의를 바꾸지 않는다. 자동 실행 스케줄도 만들지 않는다. `automatic_confirmation_enabled` 및 각 결과의 `automatic_confirmation_allowed`는 항상 false다.

`threshold_candidate=true`는 요청한 **실험 정책** 아래 구조 검사를 통과했다는 뜻이다. 검증된 모델, 보정 확률, 확정 가능성, 운영 활성화 승인이라는 뜻이 아니다. `model_versions`는 검색 범위이며 threshold에는 기본값이 없다. 후처리 decision score를 model confidence로 대체하지 않는다.

## 저장
`POST /api/auto-decisions/shadow`

```json
{
  "request_key": "opaque-client-generated-key",
  "event_ids": ["EVT_..."],
  "threshold": 0.9,
  "model_versions": ["explicit-experimental-model"],
  "ppe_types": ["helmet"],
  "policy_label": "offline-trial-1"
}
```

0.9는 예시이지 권고값이 아니다. preview의 필수 정책과 동일하며 최대 20개 사건을 원자적으로 기록한다. `request_key`는 1~100자의 영문·숫자·underscore·hyphen이고 actor별로 구분한다. 사용자·사건 정보가 아닌 불투명 임의 키를 권장한다. 클라이언트가 준 결과/fingerprint/활성화 플래그는 허용하지 않는다.

- 새 요청: 201, `created=true`.
- 같은 관리자와 같은 키, 같은 요청: 200, `created=false`. 최초 이력을 재생하며 재평가하지 않는다.
- 같은 키에 다른 정책 또는 다른 사건 목록/순서: 409 `shadow_request_key_conflict`. 새 실험에는 새 키를 사용한다.
- batch 중 사건 하나라도 없으면 404, header와 결과 모두 rollback.
- 진단 계산 실패도 부분 기록을 남기지 않는다.
- 미로그인 401, 비관리자 403, 잘못된 입력 422.

응답에는 persisted=true, run_id, actor_id, request_key, created_at, policy, item_count, removed_item_count, items, limitations가 있다. 각 item은 preview와 같은 진단 결과이며 raw 영상/썸네일 경로, 원본 미디어 내용, 비밀번호나 토큰은 저장하거나 반환하지 않는다.

이미 사람 검토를 받은 사건, review_only, 미완료/실패/만료 증거, 열려 있는 연속 사건도 **제외 사유가 있는 진단**으로 기록할 수 있다. 이는 사건 종료/전체 증거 조건을 우회한 자동결정이 아니며 candidate는 false다. 관리자 재처리와 판정 번복의 현재 경로는 유지된다.

## 조회
- `GET /api/auto-decisions/shadow/{run_id}`: 해당 역사적 평가 묶음. 없으면 404.
- `GET /api/auto-decisions/shadow?event_id=...&limit=20&offset=0`: 해당 사건의 이력. limit 1~100, offset 0~10000. created_at/run_id 내림차순. 사건이 삭제됐으면 404.
- 모두 관리자 전용이다. 목록은 offset 방식이므로 새 기록이 생긴 동안 페이지 이동 시 중복/이동 가능성이 있다. 실시간 전체 수집 커서가 아니다.

## 스냅샷과 동시성의 한계
전체 batch는 하나의 DB transaction snapshot에서 계산·저장된다. PostgreSQL은 REPEATABLE READ이며 동일 request_key 경쟁은 INSERT ON CONFLICT gate와 serialization retry로 처리한다. SQLite는 BEGIN IMMEDIATE로 쓰기를 직렬화한다.

이력은 **그 transaction의 DB snapshot을 본 평가**이지 commit 순간 최신 상태를 보장하지 않는다. PostgreSQL의 잠금 대기 동안 다른 사건 데이터가 바뀌었어도 다른 request_key의 insert-only 진단에는 serialization failure가 나지 않을 수 있다. 파일 존재 조회는 DB snapshot과 원자적이지 않다. `evaluated_at`는 계산 시각이지 DB snapshot 획득 시각이나 증거 바이트 무결성 증명이 아니다.

조회 결과는 현재 사건과 다를 수 있다. fingerprint는 실험 입력 비교용이며 캐시 키·쓰기 승인·동시성 토큰이 아니다. 실제 자동판정을 도입할 때는 사람 판단/증거 세대/정책 버전 및 검증 적격성을 **동일 쓰기 시점**에 다시 확인하고 별도 판정 이력과 재집계를 구현해야 한다.

## 삭제·보관
진단 결과 item은 candidate_event에 ON DELETE CASCADE로 연결되어 사건 삭제 시 함께 제거된다. run header에는 원문 event_ids나 증거 결과가 없으며 멱등 재시도를 위해 요청 digest와 실험 정책/actor/time/count만 남는다. 따라서 일부 사건 삭제 후 같은 요청을 재시도해도 제거된 결과를 다시 만들지 않고 `removed_item_count`로 표시한다. 영상 보관기간 만료처럼 영상만 삭제하는 기존 동작은 사건 metadata와 마찬가지로 과거 진단 이력을 유지한다. 헤더의 장기 보관/정리 정책은 별도 운영 정책이며 이 구현은 자동 삭제 주기를 정하지 않는다.

## migration / 검증
v27은 auto_decision_shadow_run/item만 추가한다. preview API는 그대로이며 기존 사건/사용자/통계의 변경이나 backfill은 없다. 업그레이드 반복, 기존 행 보존, 멱등·충돌, batch rollback, 두 thread 경쟁, 권한, 사건 삭제 cascade, 사람 검토 제외, 경로 비노출을 테스트한다. 실제 PostgreSQL 실행 여부는 테스트 보고에서 별도로 구분한다.

남은 W16 작업: 모델/PPE별 검증 자료와 합격기준, 승인된 임계값, 특이상황 판정, 후처리 점수 계약, 실제 자동확정 writer와 사람 우선권·판정 이력·재집계, 실제 장비 검증. 이 단계만으로 W16 완료라고 표시하지 않는다.
