# W07 / W13 / W17: 확정 범위와 계약 결정 전 점검

이번 조사에서는 운영·원격·프론트·탐지 모델·점수/순위를 변경하지 않았다. 안정 백엔드 기준 별도 작업본에서 기존 계약과 실패 사례를 확인했다. W07의 optional expected_scope 보호만 호환성을 유지해 구현했다. W13/W17과 강제 전환 정책은 제안이며 아직 구현하지 않았다.

## W07: 추천 대상과 편집 중 교육자료 연결

### 재현 및 현재 보호

현재 추천 ID는 분기와 rank로 생성되어 순위 재계산 후 다른 구역/PPE를 가리킬 수 있다. 격리 DB에서 사용자가 프레스 구역 추천을 보고 작성하던 중 같은 REC_CURRENT를 자재 이동 구역 대상으로 바꾸고 기존 본문을 수동 POST했더니 201과 함께 자재 이동 구역 초안으로 저장됐다.

- 저장된 material 자체는 quarter/zone/PPE/topic snapshot을 보관하며 별도 material ID와 version으로 편집한다.
- AI 생성 시작과 완료 사이에는 expected_scope 비교가 이미 있어 대상 변경을 409로 막는다.
- 수동 신규 초안 POST에는 사용자가 편집을 시작할 때의 scope/version이 없다. 서버는 의도를 추측할 수 없다.
- FE의 선택 변경/새로고침 중 unsaved draft 처리도 BE만으로 해결할 수 없다.

### 권장 통합 계약

1. 추천의 정체성은 rank가 아니라 명시적 대상 scope와 분리한다. rank는 정렬 속성이다. quarter/zone/PPE 외 education_topic 변경을 새 identity로 볼지 version 변경으로 볼지 정해야 한다.
2. 추천 조회 응답에 서버 발급 target identity와 revision 또는 opaque scope token을 포함한다.
3. 수동 초안 생성·AI 생성 요청에서 편집 시작 시 identity/revision을 함께 전송한다. 서버는 쓰기 transaction 안에서 현재값과 비교하고 다르면 409를 반환한다.
4. UI는 선택된 대상 변경 시 dirty draft를 확인·보존·분리하고 다른 대상에 묵시적으로 저장하지 않는다. 409 뒤 자동 덮어쓰기나 자동 재시도는 하지 않는다.
5. 기존 material ID 편집은 이미 고정 snapshot/version을 사용하므로 rank 재사용과 연결하지 않는다.

### 호환 단계 선택

- **선행 BE 옵션:** 새 필드를 optional로 받아 제공된 경우만 검증하면 기존 FE를 깨지 않고 새 FE를 준비할 수 있다. 하지만 구 FE는 여전히 보호되지 않으며 W07 완료가 아니다.
- **강제 보호 옵션:** 해당 필드를 필수화하면 구 FE 요청을 거절하므로 동시 FE 배포 또는 명시적 전환 게이트가 필요하다.
- 추천 ID를 즉시 바꾸거나 기존 ID를 임의 대상에 alias하면 캐시/선택/초안 문제가 남거나 새 모호성이 생긴다. 이관·구 ID 거절/안내 기준을 먼저 정한다.

검증: rank 교체·선택 변경·재계산 동시 저장에서 잘못된 대상에 쓰지 않음, 같은 대상 변경없음 성공, AI 생성 중 대상 변경 409, 기존 material 보존/버전충돌/게시 권한 회귀. W07 통합 보호 전 W01/W02 점수·순위를 바꾸지 않는다.

## W13: 구역 작업자 구성원 동시 편집

대상은 camera-group이 아니라 `PUT /api/zones/{zone}/members`와 구역 구성원 화면이다. 현재 확정 계약은 전체 user_ids 명단 교체이며 다른 구역의 배정은 유지한다. DB 쓰기 transaction은 개별 요청의 원자성을 지키지만 stale UI 목록을 식별하지 않는다.

격리 재현: 두 관리자가 빈 명단을 본 뒤 A가 worker01, B가 worker02 명단으로 저장하면 모두 200이고 최종 명단은 worker02뿐이다. 이는 write-lock만으로 고칠 수 없다.

사용자 목표는 두 관리자 변경을 보존하거나 명시적으로 거절하는 것이다. 방식은 아직 정해지지 않았다.

- **옵션 A: 구성원 revision + expected_version.** 전체 편집 UI와 잘 맞으며 오래된 명단은 409. membership을 바꾸는 모든 경로(구역 전체 교체, 사용자 zone_names/scalar 변경, 신규 계정 배정)를 같은 revision에 반영해야 한다. 구역별 revision만 둘지 전역 membership revision을 둘지, 개별 사용자 경로와의 상호 충돌 규약이 필요하다.
- **옵션 B: 개별 추가/제거 API.** 서로 다른 사람에 대한 추가/제거는 병합할 수 있고 중복 동일 요청은 idempotent 처리한다. 하지만 기존 전체 교체 경로가 남으면 여전히 새 변경을 지울 수 있으므로 FE 전환/구 경로 제한이 필요하다. 같은 구성원에 대한 추가와 삭제의 경쟁은 CAS나 명시적 최종연산 규약이 필요하다.

현재 화면이 전체 명단을 보내므로 A가 통합 변경량이 작다. optional version만 추가한 BE는 호환 준비일 뿐 구 FE 보호가 아니다. 필수화 또는 delta 전환은 FE 연결을 포함해 결정해야 한다.

검증: 같은 version의 두 전체 교체 중 하나만 성공, 사용자 zone_names 경로와 구역 전체 명단의 경합, 다른 구역 보존, 비활성 작업자 유지, 관리자/없는 계정·구역 거절, 중복 재시도, 구역 삭제와 배정 경합, PostgreSQL 실제 동시성.

## W17: 동일 사건 의심 관계 표시

확정 범위: 같은 camera group, 같은 PPE, 촬영/관측 구간 겹침을 metadata로 비교해 의심 관계를 표시한다. 자동 병합이나 동일 인물 확정이 아니며 사건·영상은 별도 보존한다. 수동 통합 전에는 각각 확정 이벤트 건수로 집계한다.

현재 camera group은 참고 영상 요청을 만들고, manual merge는 별도 관리자 절차로 존재한다. 의심 관계 API/DB는 없다.

다음 선택이 아직 필요하다.

- 관계를 조회 시 계산할지 영속 기록할지. 현재 카메라 그룹 변경이 과거 사건 태그를 바꾸는지, 당시 group snapshot을 사용할지.
- 비교에 사건의 positive 관측 구간을 쓸지, gap을 뺀 구간을 쓸지. pre/post-roll 포함 영상 범위를 사건 겹침으로 오인하지 않아야 한다.
- 검토·삭제·수동 병합·태그 해제 이후 표시 및 재계산. 수동 해제 영속성·과거 backfill·TTL·clock 오차 허용값은 미정이다.
- 표시 필드/API, 페이지 밖 사건과의 관계, 최대 조회 범위/성능, FE에서 '같은 사람 확정 아님' 안내.

안전한 기본 원칙은 자동 병합·통계 감산·확정 점수 변경을 하지 않는 것이다. 그러나 그 원칙만으로 위 lifecycle/표시 정책을 결정할 수 없으므로 자동 태그를 연결하지 않았다. 별도 순수 후보 계산 함수부터 테스트할 수는 있지만 연결 계약을 확정하지 않은 상태의 미사용 함수는 완성 기능으로 볼 수 없다.

검증: 다른 group/PPE/비겹침 배제, 정확 경계 touching의 정의, 실제 positive와 pre/post 범위 구분, 사건 별도 보존·건수 유지, 그룹 변경/병합/삭제/해제 후 규약, 페이지 경계, 과거 데이터 및 DB 성능.

## 다음 결정

우선 W07의 target/revision과 FE dirty-state 보호를 함께 정하고 연결한다. W13은 CAS 전체교체와 delta 중 하나를 선택하며 모든 membership mutation 경로를 포함한다. W17은 사용자에게 필요한 표시·해제·이력 정책을 정한 뒤 read-only 후보 관계부터 연결한다. 이번 조사에서 확정되지 않은 정책을 임의 기본값으로 추가하지 않았다.


## W07 선행 BE 구현 및 현우 인계 (부분 완료, 전체 W07 미완료)

기존 URL·응답·추천 ID·순위·저장 material 구조는 유지한다. 다음 선택 필드만 추가했다.

`POST /api/recommendations/{recommendation_id}/materials` 기존 본문에:

```json
{
  "title": "안전모 점검",
  "body": "검토할 본문",
  "checklist": ["확인 항목"],
  "expected_scope": {
    "recommendation_id": "EDU_2026Q3_01",
    "quarter": "2026-Q3",
    "zone_name": "프레스 구역",
    "ppe_type": "helmet",
    "education_topic": "안전모 착용 교육"
  }
}
```

`POST /api/recommendations/{recommendation_id}/materials/generate`는 기존 body 없는 요청을 유지하며 새 FE는 `{ "expected_scope": { 위 다섯 필드 } }`를 보낼 수 있다. snapshot의 quarter는 추천 목록 응답 최상위 quarter에서, 나머지 네 필드는 선택한 items 항목에서 복사한다. 미배정 구역의 빈 zone_name도 그대로 전달하며 임의 기본 구역으로 바꾸지 않는다. rank/score/generated_at을 섞지 않는다. 선택 순간의 snapshot을 편집 상태와 함께 고정하며 저장 직전 최신 추천값으로 덮어쓰면 보호를 우회한다.

- snapshot이 현재 URL 대상과 정확히 일치하면 기존처럼 201.
- 대상이 삭제·재사용·변경됐으면 `409 education_recommendation_changed`, 저장 없음. 생성 요청은 이 단계에서 provider를 호출하지 않아 불필요한 생성 비용도 피한다.
- 생성 중에도 대상이 바뀌면 기존 완료 시 재확인으로 409, 잘못된 초안은 저장하지 않는다.
- snapshot의 일부 필드 누락/알 수 없는 필드는 422.
- 기존 요청에서 expected_scope를 생략하거나 null이면 기존 동작이다. **구 FE는 여전히 stale editor 오귀속에 취약하다. 이 패치만으로 W07 완료라고 표시하지 않는다.**

FE 후속: 선택 변경/새로고침 dirty-state 처리, snapshot 고정 전달, 409에서 편집내용 보존 및 대상 재선택/명시 확인, 오래된 비동기 응답의 현재 편집기에 덮어쓰기 차단을 연결해야 한다. 강제 snapshot 전환·안정 ID/버전 정책은 아직 미구현이다. 모든 클라이언트 연결을 검증하기 전 점수/순위 W01/W02 변경은 보류한다.
