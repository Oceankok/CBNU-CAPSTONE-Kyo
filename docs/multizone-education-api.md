# 여러 구역 통합 교육자료 API (v1, #239)

## 범위와 결정

- 같은 분기·같은 PPE에 속하는 **서로 다른 2~20개 구역**의 추천을 선택한다. 혼합 PPE는 별도 자료로 생성한다.
- 기존 단일 구역 교육자료 API/DB/작업자 `groups` 응답은 변경하지 않는다.
- 원본 추천 ID, 분기, 구역, PPE, 주제 및 당시 건수·점수·점수 버전을 자료에 스냅샷으로 보존한다. 순위/생성시각은 범위 동일성에 포함하지 않는다.
- 통합 건수는 선택한 추천의 `confirmed_count` 합계이며, 서로 다른 사람 수나 카메라 중복 제거 결과라는 의미가 아니다. 위험 점수는 합산하거나 재가중하지 않고 구역별 기존 수치를 그대로 보여준다.
- 원본 추천이 갱신/삭제되어도 통합 자료 기록은 삭제하지 않는다. 신규 작성/게시 시에는 현재 추천과 스냅샷이 같아야 한다.
- AI 초안 생성에는 허용 목록의 PPE 코드와 애플리케이션에 정의된 해당 PPE의 일반 교육 주제만 전달한다. 원본 추천 주제에는 구역명이 포함될 수 있으므로 원본 자유 텍스트는 전달하지 않는다. 따라서 구역별 특수 지침은 AI 초안에 자동 반영되지 않으며 관리자가 직접 검토·보완한다. 구역명, 통계, 사용자/카메라 정보는 전달하지 않는다. 공급자 설정과 기존 remote worker 경로를 재사용하며 AI 생성은 게시가 아니다.
- 번역은 이 계약에 포함하지 않는다. 별도 단일 구역 자료 번역 기능과 독립적이다.

## 관리자 API

모든 경로에 기존 관리자 인증 필요. `POST /api/education/bundles/preview`:

```json
{"recommendation_ids":["EDU_S1_a","EDU_S1_b"]}
```

응답: `quarter`, `ppe_type`, `sources` (위 스냅샷 필드의 배열), `snapshot` (64자 digest), `confirmed_count_sum`.

`POST /api/education/bundles`:

```json
{"recommendation_ids":["EDU_S1_a","EDU_S1_b"],"expected_snapshot":"<preview의 snapshot>","title":"통합 교육","body":"관리자 검토용 본문","checklist":["착용 확인"]}
```

`POST /api/education/bundles/generate`는 같은 선택/expected_snapshot만 받는다. 201 draft 반환. 기존 공급자가 비활성 상태이면 오류를 반환하며 임의 자료를 만들지 않는다. 지원하지 않는 PPE 코드는 422 `education_bundle_unsupported_generation_ppe`로 수동 작성을 안내한다. 생성 대기 중 추천 변경은 최종 저장 전에 재검사하여 409 처리한다. 네트워크 생성 중 DB 쓰기 잠금을 유지하지 않는다.

두 작성 API의 응답은 `bundle_id`, `quarter`, `ppe_type`, `sources`, `snapshot`, `title`, `body`, `checklist`, `status`, `version`, 공급자 및 감사 필드이다.

- `GET /api/education/bundles?quarter=2026-Q3&limit=50&offset=0`: 해당 분기 자료 목록 `items` (limit 1~100)
- `GET /api/education/bundles/{bundle_id}`: 보존된 원문/스냅샷 조회
- `PATCH /api/education/bundles/{bundle_id}`: `title, body, checklist, expected_version`. draft만 수정
- `POST /api/education/bundles/{bundle_id}/publish`: `{"expected_version":0,"approved":true}`. 원본 재검사 후 게시
- `POST /api/education/bundles/{bundle_id}/unpublish`: `{"expected_version":1}`. 게시 취소 후 draft

모든 변경은 version을 증가시킨다. 같은 분기/PPE/정확히 같은 구역 집합의 게시본은 하나만 유지하고 이전 자료는 superseded로 보존한다. 일부만 겹치는 구역 집합은 별도 자료이므로 동시에 게시 가능하다. 동일 구역 집합의 순서는 무관하다.

오류: 401/403 인증, 404 자료 없음, 409 선택/버전 변경 또는 상태 충돌, 422 잘못된 구역 조합/본문, 429 AI 생성 중, 기존 공급자 502/503 등. 409 선택 변경 시 preview부터 다시 받고 관리자에게 변경을 보여준다. 자동 재게시 금지.

## 작업자 API

`GET /api/worker/education-bundles` → `{"items":[...]}`. 관리자 역할로 호출하면 403.
각 항목은 `bundle_id, quarter, ppe_type, assigned_zones, title, body, checklist`만 포함한다.

현재 배정 구역 중 원본의 대상 구역과 교집합이 하나라도 있고, 그 구역의 최신 추천 분기가 자료 분기와 같고 주제가 여전히 일치할 때만 보여준다. 미배정/해제/게시 취소/다음 분기 전환은 즉시 제외된다. 여러 배정 구역에 걸쳐도 bundle은 한 번만 반환한다. `assigned_zones`는 현재 본인에게 해당되는 구역만 표시한다. 내부 건수, 점수, 전체 대상 구역, 관리자/원본 추천 정보는 반환하지 않는다.

대상 구역 중 하나라도 조건을 충족하면 통합 본문 전체를 공유한다. 본문을 구역별로 나눠 비공개 처리하는 기능은 아니다. 관리자가 입력한 본문 자체는 그대로 전달되므로 게시 전 내부 통계나 비공개 구역정보를 본문에 포함하지 않았는지 검토해야 한다. 콘텐츠는 공통 PPE 교육으로 작성한다.

## 프론트 요청

1. 같은 분기/PPE 추천 다중 선택, 선택 범위와 구역별 통계/합계를 관리자에게 표시.
2. preview snapshot을 보관하여 수동 작성 또는 생성, 생성 완료 후 편집/관리자 검토.
3. 게시/게시 취소 버튼에 expected_version, 명시적 approved 사용. 409 변경 안내/재조회.
4. 기존 단일 구역 UI는 유지하고 작업자 페이지에 별도의 통합 교육 카드 추가. 작업자 API만 사용하고 관리자 sources를 재사용하지 않기.
5. 현재 버전은 단일 언어 원문. 통합자료 번역 UI는 후속 계약 이후 추가.

## 적용/검증

SQLite/PostgreSQL migration 32. 실제 DB/서버에는 적용하지 않음. 번역 브랜치의 migration 33과 통합할 때 두 순차 마이그레이션 및 PostgreSQL 허용 버전 목록을 함께 보존한다. PostgreSQL 실통합 테스트는 별도 전용 DB가 있어야 하며 SQLite 테스트 통과가 이를 대체하지 않는다.

현재 작업자 목록은 게시 자료 전체를 검사하는 v1 구현이다. 대규모 운영 전에는 구역 관계 테이블/쿼리 및 페이지네이션 최적화가 필요하다. PostgreSQL 최초 동시 게시의 unique 충돌은 409로 반환하며 새 조회 후 명시적으로 재시도한다.
