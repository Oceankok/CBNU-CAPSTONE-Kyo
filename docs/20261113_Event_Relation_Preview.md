# W17 백엔드 선행: 동일 사건 의심 메타데이터 미리보기

상태: **read-only 후보 조회 준비**, 저장 태그/관리자 해제/프론트 연결을 포함한 W17 전체 완료가 아니다. 기존 사건·영상·통계·수동 병합은 변경하지 않는다. 모델·가중치·프론트·운영 배포는 변경하지 않았다.

## 확정 범위와 이번 제한
같은 카메라 그룹, 같은 PPE, 겹치는 관측구간은 동일 사건일 가능성을 표시하는 근거일 뿐 같은 사람/사건임을 확정하지 않는다. 미리보기의 두 사건은 계속 별개로 유지되며 확정된 경우 각각 집계한다. 자동 병합이나 통계 감산은 절대 하지 않는다.

이번에는 **다른 카메라의 사건**만 비교한다. 같은 카메라 중복 송신 처리는 기존 멱등성에 맡긴다. 시계 오차 허용값을 새로 정하지 않고 정확한 UTC 구간에서만 양의 겹침을 비교한다. 구간 끝과 시작이 접하기만 하면 후보가 아니다. pre/post-roll 영상 길이가 아니라 candidate_event의 관측 시작·종료를 쓴다. 다만 이 외곽구간에는 중간 가림/관측 공백이 포함될 수 있으므로 실제 모든 순간이 관측됐다고 해석하면 안 된다.

## 새 등록 문맥, 기존 자료를 추정하지 않음
- v26 additive migration: `event_group_context`, `event_group_membership_snapshot` 및 조회 인덱스.
- 정상 사건 등록 transaction에서 현재 camera group ID와 version을 한 번 기록한다. 그룹이 없었음도 context와 빈 snapshot으로 명시한다.
- 과거 사건에는 snapshot을 역산하지 않는다. missing과 known-empty를 구별한다.
- 등록 시점 snapshot이지 지연 도착한 영상의 촬영 시점 그룹을 복원하는 것은 아니다.
- 현재 그룹 구성/이름 변경이나 삭제는 기존 snapshot을 바꾸지 않는다. 같은 group ID와 version이 모두 맞는 사건만 비교한다. 이름만 바뀌어도 version이 달라지므로 일부 후보를 놓칠 수 있는 보수적 계약이다.
- 그룹 ID/version에 live 그룹 외래키를 걸지 않아 그룹 삭제가 과거 문맥을 지우지 않는다. 사건 삭제 시에는 관련 snapshot이 함께 삭제된다.
- 직접 SQL로 추가된 자료는 snapshot이 없을 수 있으며 이를 정상 등록 자료처럼 추정하지 않는다.

## API
`GET /api/events/{event_id}/suspected-duplicates/preview?limit=20&offset=0`

관리자 인증 필요. limit 1~100, offset 0~10000. 없는 사건 404, 미인증401/일반작업자403, 잘못된 pagination422.

```json
{
  "event_id": "EVT_...",
  "mode": "metadata_preview",
  "scope": "registered_group_version",
  "interval_basis": "observation_envelope",
  "clock_tolerance_seconds": 0,
  "persisted": false,
  "automatic_merge": false,
  "statistics_changed": false,
  "eligible": true,
  "reason": null,
  "total": 1,
  "limit": 20,
  "offset": 0,
  "has_more": false,
  "next_offset": null,
  "pagination_truncated": false,
  "items": [{
    "event_id": "EVT_other",
    "camera_id": "CAM_002",
    "ppe_type": "helmet",
    "timestamp_start": "2026-11-12T10:00:00.000000+00:00",
    "timestamp_end": "2026-11-12T10:00:10.000000+00:00",
    "event_status": "pending",
    "overlap_seconds": 10,
    "shared_group_snapshots": [{"group_id": "GRP_...", "group_version": 0}],
    "reason": "same_registered_group_version_ppe_overlapping_observation"
  }]
}
```

eligible=false일 때 items는 빈 배열이고 reason이 다음 중 하나다.
- registration_group_snapshot_unavailable: 등록 당시 문맥 없음(legacy 포함)
- observation_timezone_unknown: 관측 시간대 불명
- positive_observation_interval_unavailable: 양의 시간 구간 없음
- no_registered_camera_groups: 등록 당시 그룹 없음
- event_was_merged: 이미 다른 사건으로 수동 병합된 원본

최근 사건 N개만 비교하는 것이 아니라 indexed snapshot group 교집합과 겹침 조건으로 DB 전체에서 count/페이지를 조회한다. 정렬은 관측시작 내림차순, event ID 내림차순이다. 한 응답의 count와 items는 같은 읽기 snapshot을 사용하지만 페이지 사이 새 사건 유입에 대한 cursor 고정은 제공하지 않는다. 다음 요청에는 next_offset을 사용하며, 최대 offset10000을 넘어야 할 때 has_more=false/next_offset=null/pagination_truncated=true로 잘림을 명시한다. total은 이 조회 상한과 무관한 전체 조건 일치 건수다. 화면 목록에 없는 사건도 결과에 포함될 수 있다.

## 프론트 연결
- 이벤트 상세의 선택적 '동일 사건 의심 미리보기'에서 호출한다. 같은 사람 확정, 자동 병합, 중복 제외된 건수로 표시하지 않는다.
- eligible=false와 eligible=true/total=0을 구분한다. 전자는 근거가 없어 판정하지 못한 것이고 후자는 이번 계약 범위의 후보가 없는 것이다.
- 미리보기에는 태그 저장/해제 동작이 없다. UI에 동작하지 않는 해제 버튼을 추가하지 않는다.
- 기존 수동 병합 후 다시 조회하면 source_event_id는 제외된다. 관리자 검토 상태만 바뀌어도 사건 자체는 사라지지 않으므로 상태를 함께 표시한다. 상태를 근거로 같은 사건 여부를 자동 확정하지 않는다.

## 남은 정책과 검증
영속 태그와 관리자 해제를 구현하려면 해제 기록의 재계산 규칙, 과거자료 처리, 시계오차 허용, 관측 공백 활용을 별도 결정해야 한다. 이 preview 자체는 그 정책을 임의 확정하지 않는다. 후속 [명시적 evidence 저장/해제 API](20261113_Saved_Event_Relations.md)는 별도 기록의 저장·해제만 제공하며, preview 후보 억제나 영구 dismiss 정책은 구현하지 않는다.

실제 PostgreSQL26 이관/동시성 및 실카메라 시계오차 검증은 이 patch에서 아직 수행하지 않았다. 이전108파일 PG419 통과와 구분한다. SQLite 초점 테스트는 같은/다른 그룹·버전/PPE/카메라/시간경계, 페이지, 권한, merge, 이관 보존, 등록 rollback/삭제cascade 및 사건 상태 무변경을 검증한다.

이번 후보 검증: 초점7개 PASS, 전체 백엔드426개 OK(실제PG6개 skip). 독립 코드리뷰의 페이지 상한 지적을 수정하고 재확인했다. 해당 결과는 SQLite 합성 환경이며 실제 PostgreSQL v26 적용·시계보정·대규모 성능 증거는 아니다.
