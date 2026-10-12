# 작업자 다중 구역 API — #127

## DB 및 호환 정책

SQLite v7에서 `user_zone(user_id, zone_name)`을 추가합니다. 기존 작업자 `app_user.zone_name`을 이관하고, 이후 다중 구역 관계를 기준으로 처리합니다. 기존 scalar는 구역명 정렬 기준 첫 구역 또는 null로 동기화합니다. 관리자에게 구역을 배정하지 않습니다.

마이그레이션은 기존 데이터·비밀번호·교육 기록을 유지합니다. 존재하지 않는 구역을 참조하는 작업자가 있으면 이관을 중단하고 정정을 요구하며 해당 배정을 조용히 제거하지 않습니다. 기존 DB를 실제로 업그레이드하는 작업은 아직 수행하지 않았습니다.

## 관리자 API

- `GET /api/users`: 각 사용자에 `zone_names: string[]`을 추가합니다. 기존 `zone_name`·활성 상태 등은 유지합니다.
- `POST /api/users`, `PATCH /api/users/{user_id}`: `zone_names` 배열로 전체 배정을 지정할 수 있습니다. 빈 배열은 배정을 모두 해제합니다. 중복·없는 구역·null 배열·관리자 배정은 422입니다.
- 기존 `zone_name` 입력은 **전체 배정을 해당 한 구역 또는 빈 목록으로 교체**합니다. 단순히 한 구역에 추가할 때는 아래 구역 members API를 사용합니다. 두 입력 필드를 함께 보내면 422입니다.
- `PUT /api/zones/{zone_name}/members`: `{ "user_ids": ["worker01", "worker02"] }`로 해당 구역의 작업자 목록을 전체 교체합니다. 다른 구역 배정은 유지합니다. 응답은 `{ "zone_name": "…", "user_ids": […] }`입니다.
- members 요청은 중복·공백 ID, 존재하지 않는 계정·관리자 계정이 있으면 422이며 전체 변경을 취소합니다. 구역이 없으면 404입니다. 비활성 작업자의 배정은 유지·수정할 수 있으며 비활성 상태에서 로그인할 수는 없습니다.
- `GET /api/zones`: `member_count`를 추가합니다. 비활성 계정도 배정 인원에 포함합니다. 사용자 목록의 `zone_names`로 구역별 배정 대상을 표시할 수 있습니다.
- 구역 삭제는 해당 구역에 작업자가 남으면 409입니다. 기존 카메라·장비 연결 제한도 유지합니다.

## 작업자 API

- `GET /api/worker/zones`: `{ "items": [ZoneRule, …] }`, 배정된 모든 구역을 구역명 순서로 제공합니다. 미배정 시 빈 배열입니다. 관리자 호출은 403입니다.
- 기존 `GET /api/worker/zone`: 첫 구역 조회로 유지합니다. 미배정은 기존처럼 404입니다.
- `GET /api/worker/education`: `groups`에 배정된 구역별 추천을 제공합니다. 각 구역에 존재하는 최신 분기를 독립적으로 선택합니다. 다른 구역의 추천·관리자용 점수·통계는 제공하지 않습니다.

```json
{
  "quarter": null,
  "zone_name": "A",
  "items": [],
  "empty_reason": "recommendations_unavailable",
  "groups": [
    {"quarter": null, "zone_name": "A", "items": [], "empty_reason": "recommendations_unavailable"},
    {"quarter": null, "zone_name": "B", "items": [], "empty_reason": "recommendations_unavailable"}
  ]
}
```

위 예시는 배정된 두 구역에 추천이 없는 경우입니다. 기존 최상위 `quarter/zone_name/items/empty_reason`은 첫 그룹의 값으로 유지합니다. **모든 구역을 표시하는 신규 화면은 `groups`를 사용합니다.** 미배정이면 `groups=[]`, `empty_reason=zone_unassigned`입니다. 자료 본문은 기존과 같이 `material=null`이며 교육 콘텐츠 제작은 별도 범위입니다.

구역별 PPE는 카드별로 표시하는 계약을 사용하며 합집합으로 자동 통합하지 않습니다. 로그인 사용자 배정과 영상 속 사람을 연결하는 기능은 포함하지 않습니다.

## 프론트 연동

프론트 화면은 수정하지 않았습니다. 구역 카드에서 사용자 추가·제외 시 대상 구역의 원하는 작업자 ID 목록을 members API로 전송합니다. 기존 개별 사용자의 scalar PATCH는 다른 구역 배정을 제거하므로 다중 구역 화면에서는 사용하지 않습니다. 작업자 화면은 `/worker/zones`와 교육 `groups`를 사용합니다.

## 검증 계획

추가 테스트는 다른 구역 배정 유지·삭제 제한·관리자/잘못된 사용자 원자적 거절·구버전 scalar 교체·v6 이관 재실행·구역별 최신 교육과 정보 노출 제한을 확인합니다. 기존 교육 테스트는 단일 scalar SQL 조작 대신 새 관계로 fixture를 준비하며 최신 분기·구역 격리·허용 필드 검증 의도는 유지합니다.

2026-10-12 승인 후 임시 SQLite DB에서 다중 구역·교육 테스트 8개와 백엔드 전체 회귀 테스트 67개가 통과했습니다. 실제 개발 DB·GPU·방송·Docker는 사용하지 않았습니다. Docker 브랜치의 runtime 테스트 3개는 이 브랜치에 포함되지 않아 별도 검증 결과로 관리합니다. PostgreSQL 전환은 이 스키마를 반영한 뒤 진행합니다.
