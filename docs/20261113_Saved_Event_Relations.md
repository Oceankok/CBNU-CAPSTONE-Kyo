# W17 관리자 저장 의심관계: evidence bookmark 계약

## 범위와 중요한 미완료 사항

자동 병합/확정/집계 감산/자동저장/과거 backfill 없음. 원본 사건·영상·각각 집계 그대로다.
**release는 저장된 관계 기록만 해제한다. preview 후보를 숨기거나 억제하지 않는다.**
원래 기대할 수 있는 '의심표시 해제', 영구 suppression, 증거 변경 후 재제안, 자동 태그 정책은 여전히 미결이다. 이 API가 W17 전체 완료라는 뜻이 아니다.

관리자가 현재 동일 증거를 명시적으로 저장할 수 있는 최소 API다. 그룹/PPE/서로 다른 카메라/양의 관측 outer interval 겹침 규칙은 기존 preview 그대로이며 모델은 변경하지 않는다. 저장 시점에도 사건이 열려 있으면 이후 구간 확장으로 stale가 될 수 있다. stale는 저장 오류나 동일사건 부정이 아니라 '지금 규칙 입력과 저장 증거가 다름'이다.

## 모든 endpoint는 관리자 전용

- `GET /api/events/{event_id}/suspected-duplicates/evidence/{peer_id}`
  - 현재 pair의 `eligible`, `reason`, `fingerprint`, `evidence` 조회.
  - fingerprint는 정렬된 양쪽 사건 ID/카메라/PPE/시작/끝, 공통 등록 그룹 ID+버전, 규칙 버전/구간 기준/시계 허용오차0의 SHA256. 순서를 뒤집어도 같다.
  - 증거를 만들 수 없으면 eligible=false/fingerprint=null. 같은 ID422, 없는 사건404.
- `POST /api/events/{event_id}/saved-suspected-relations/{peer_id}`
  - body: `{"expected_fingerprint":"<64 lowercase hex>"}`. 서버가 잠금 후 재계산해 비교, 달라졌거나 부적격이면409.
  - 입력 fingerprint는 권한 토큰이 아니다. 인증된 관리자만 저장 가능.
  - 같은 fingerprint 재시도는 같은 기록 반환. **이미 released면 released 그대로며 자동 재활성화하지 않는다.** 새 증거 fingerprint의 저장은 별도 기록이다. 자동 새 기록 생성 없음.
- `GET /api/events/{event_id}/saved-suspected-relations?limit=20&offset=0`
  - saved/released 모두 반환. total, items, has_more, next_offset, pagination_truncated. limit1..100/offset0..10000. 정렬 saved_at DESC, relation_id DESC. 실시간 삽입 중 offset 페이지 경계 이동 가능.
- `POST /api/events/saved-suspected-relations/{relation_id}/release`
  - body는 저장된 기록의 expected_fingerprint. 다른 fingerprint409, 없는 기록404.
  - 현재 증거가 바뀌어 stale여도 정확히 그 과거 저장 기록을 해제할 수 있다. 첫 해제 actor/time 유지, 반복해제 멱등.
  - 저장 기록 상태만 변경. **기존 preview 결과가 그대로 다시 보이는 것이 계약상 정상이다.**

저장/해제 응답은 relation_id/event_a/event_b, fingerprint/evidence, saved_by/at, released_by/at, state(saved|released), stale, current_fingerprint/reason, suppresses_preview=false, automatic_merge=false, statistics_changed=false를 포함한다.

## stale와 보관

현재 증거 fingerprint 불일치, 겹침 해소, 병합 source 등 부적격이면 stale=true. 일반 review 상태 변경은 metadata 규칙 입력이 아니므로 stale가 되지 않는다. 현재 카메라 그룹 편집/삭제는 과거 등록 snapshot을 바꾸지 않으므로 stale가 되지 않는다.

raw 영상 경로/이미지/추론결과/개인 텍스트를 저장하지 않는다. 기존 관리자 actor ID와 최소 metadata만 보관한다. 관계는 양쪽 candidate_event의 부속이며 **어느 한 사건을 실제 삭제하면 FK ON DELETE CASCADE로 저장/해제 정보도 삭제**된다. 기존 event_group_context/processing lifecycle 부속과 동일 동작. 영구 감사원장, 사건 삭제 후 tombstone 보존 기능이 아니다. 사용자의 별도 보관/영구 suppression 결정을 대신하지 않는다.

## 동시성과 DB

읽기 API는 기존 read-only snapshot transaction. 쓰기는 별도 READ COMMITTED transaction에서 schema advisory write lock을 획득한 **다음** 증거를 읽는다. REPEATABLE READ의 lock 대기 전 snapshot으로 오래된 증거를 입장시키지 않기 위함이다. SQLite는 BEGIN IMMEDIATE.
정상 증거 writer 확인: event_repository.insert_candidate_event(자체 begin_write 또는 호출자의 transaction), delete_candidate_event(begin_write), processing.lifecycle.start/update(begin_write), event_merge.service.merge(begin_write), camera_groups.routes group writes(begin_write). 등록 group snapshot은 insert transaction 내부에서만 생성된다. 동일 잠금을 따르지 않는 외부 SQL writer는 보장 범위 밖이다.

schema **v28** 예약. 이 별도 후보의 v27 W16 migration은 부모 통합 담당이 삽입한다. **이 후보를 운영 반영하거나 이 후보가 만든 임시 v28 DB를 최종 통합 검증에 재사용하면 안 된다.** v27이 없는 채 버전만28인 DB를 재사용하면 v27 migration이 생략된다. 최종 통합에서 실제 v26→v27→v28/새DB migration과 PostgreSQL 재검증 필요.

## 검증

전용 테스트9: 저장/해제/역방향 멱등, preview 무변경, 변경후409/stale/새기록, 병합후stale, 양쪽삭제CASCADE, 인증/권한/입력/페이지경계, v27→28 반복이관/no backfill, rollback/현그룹불변, PG helper 순서, 실제PG 대기후최신증거 CAS.
SQLite 집중9 OK(실제PG 경합1 skip). PostgreSQL 실서버 결과로 간주하지 않는다. 전체 회귀 결과는 최종 보고 참조.

전체 SQLite 회귀: 449 tests OK, skipped7 (PostgreSQL 전용 포함). 독립 읽기 리뷰 추가차단 없음. 실제PG와 W16 v27 통합 회귀는 아직 수행하지 않음.
