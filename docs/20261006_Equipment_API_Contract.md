# 장비 등록·후보 인식·카메라 PPE API 계약안 (#112)

## 상태와 적용 범위

이 문서는 프론트 mock 선행 개발을 위한 제안 계약입니다. 아래 신규 API는 아직 구현되지 않았으며, 합의 후 백엔드와 프론트 담당 PR에서 각각 구현합니다. 기존 `/api/zones`와 인증 계약은 유지합니다.

초기 범위는 장비 CRUD, 이미지 1장 기반 상용 VLM 후보, 관리자 확인 등록, 카메라 전체에 적용할 장비 연결, 적용 PPE 미리보기입니다. 화면 내 여러 작업 위치 구분·ROI·Re-ID·자동 PPE 추론 연동은 이번 API 계약과 분리합니다. 카메라 전체에 규칙을 적용할 수 없는 장소에는 장비를 연결하지 않고 위치별 적용 기능을 후속으로 설계합니다.

## 공통 규칙

- 모든 신규 API는 관리자 Bearer 인증을 요구합니다. 미인증·토큰 만료는 401, 작업자는 403을 반환합니다.
- 목록은 `{ "items": [...] }`로 반환합니다. 초기에는 페이지네이션을 도입하지 않으며 `zone_name`, `status` 등 아래 명시한 필터만 지원합니다.
- ID는 서버가 생성하는 불변 문자열입니다. ID 접두사와 길이에 의존하지 않습니다.
- 생성은 201, 조회·수정·비활성 처리는 200과 JSON 본문을 반환합니다. 기존 `apiFetch`가 JSON을 읽으므로 204 응답은 사용하지 않습니다.
- 업무 오류는 `{ "detail": "equipment_not_found" }`처럼 문자열 코드를 제공합니다. Pydantic 형식 검증의 422 응답은 기존 배열 형식을 유지하며 프론트 클라이언트에서 문자열로 정규화해야 합니다.
- 시각은 시간대가 포함된 ISO 8601 UTC 문자열입니다. nullable 필드는 `null`로 반환합니다.
- PPE 코드는 `helmet`, `vest`, `goggles`, `gloves`, `safety_shoes`, `hearing_protection`, `mask`, `harness`입니다. 중복·알 수 없는 코드는 422로 거절합니다. 빈 배열은 장비 추가 PPE가 없다는 명시적 설정입니다.

## 1. 장비 API

### 응답 객체

```json
{
  "equipment_id": "EQ_example",
  "name": "프레스 1호기",
  "equipment_type": "press",
  "zone_name": "프레스 구역",
  "required_ppe": ["vest"],
  "status": "active",
  "created_at": "2026-10-06T12:00:00Z",
  "updated_at": "2026-10-06T12:00:00Z"
}
```

`equipment_type`은 선택 입력인 자유 문자열이며 수동 등록 시 `null`을 허용합니다. `name`은 표시 이름입니다. `required_ppe`는 구역 기본값에 추가하는 항목이며 기본 PPE를 해제하지 않습니다. `status`는 `active`·`inactive`만 허용합니다.

| API | 동작 |
|---|---|
| `GET /api/equipment?zone_name=...&status=active` | 장비 목록을 반환합니다. 필터가 없으면 비활성 장비도 포함합니다. |
| `GET /api/equipment/{equipment_id}` | 단일 장비를 반환합니다. |
| `POST /api/equipment` | 수동으로 장비를 등록합니다. |
| `PATCH /api/equipment/{equipment_id}` | 이름·종류·구역·추가 PPE·상태를 수정합니다. |
| `DELETE /api/equipment/{equipment_id}` | 물리 삭제 대신 비활성 처리하고 장비 객체를 반환합니다. 반복 요청도 동일하게 성공합니다. |

POST 요청은 아래와 같습니다. `name`, `zone_name`, `required_ppe`는 필수이며, `equipment_type`은 생략 가능하고 `status` 기본값은 `active`입니다.

```json
{
  "name": "프레스 1호기",
  "equipment_type": "press",
  "zone_name": "프레스 구역",
  "required_ppe": ["vest"]
}
```

PATCH는 전달된 필드만 변경합니다. `name`·`zone_name`은 공백 제거 후 비어 있을 수 없으며 각각 최대 100자입니다. `equipment_type`은 최대 100자입니다. `required_ppe`는 최대 8개입니다. 필수 필드에 `null`을 전달하면 422로 거절합니다. 장비 이름 중복은 허용합니다.

존재하지 않는 구역은 `422 zone_not_found`로 거절합니다. 카메라에 연결된 장비의 구역 이동은 `409 equipment_has_camera_links`로 거절하며 먼저 연결을 해제해야 합니다. 장비 비활성화 시 연결 이력은 유지하되 적용 PPE 계산에서 제외합니다. PATCH로 다시 활성화하면 기존 연결에도 다시 적용된다는 점을 화면에 안내합니다.

## 2. VLM 후보 분석과 관리자 등록

### 분석 요청

`POST /api/equipment/recognitions?camera_id=CAM_001`에 JPEG/PNG 원본 바이트를 전송합니다. `camera_id`는 선택입니다. Content-Type은 `image/jpeg` 또는 `image/png`이며 최대 10 MiB입니다. FormData가 아닌 raw body로 전송하며, 프론트에서는 기본 JSON 헤더를 해당 이미지 형식으로 덮어씁니다.

서버는 얼굴 비식별화를 먼저 완료한 이미지만 상용 인식 API에 전송하고 관리자에게 제공합니다. 비식별화에 실패하면 외부 API를 호출하지 않습니다. 원본 이미지 URL을 반환하지 않습니다. 특정 제공업체는 계약에 고정하지 않습니다.

분석은 초기에는 동기 요청으로 처리합니다. 외부 요청 제한 시간은 기본 30초로 설정하고 중복 클릭을 차단합니다. 비동기 작업 큐는 후속 최적화로 둡니다.

입력이 유효하면 분석 기록을 생성하고 201로 반환합니다. 모델·네트워크·외부 API 실패도 아래 객체의 `status=failed`, `error_code`로 기록하여 수동 등록 fallback을 제공할 수 있도록 합니다. 파일 형식 오류·크기 초과·손상은 각각 415·413·422로 거절합니다.

```json
{
  "recognition_id": "ER_example",
  "camera_id": "CAM_001",
  "status": "analyzed",
  "candidates": [
    {
      "candidate_id": "EC_example",
      "label": "프레스",
      "equipment_type": "press",
      "confidence": null,
      "confidence_kind": "not_available",
      "reason": "작업대와 가압 구조가 확인됩니다."
    }
  ],
  "evidence_url": "/api/equipment/recognitions/ER_example/image",
  "evidence_expires_at": "2026-10-07T12:00:00Z",
  "equipment_id": null,
  "error_code": null,
  "created_at": "2026-10-06T12:00:00Z"
}
```

후보는 최대 5개입니다. 인식이 불확실하면 `analyzed` 상태에서도 빈 배열을 반환할 수 있습니다. 신뢰 수치는 제공 가능한 경우에만 0~1로 반환하고 `confidence_kind=provider_estimate`로 표시합니다. 모델이 제시한 수치를 검증된 정확도로 표시하지 않습니다. 수치가 없으면 `null`·`not_available`로 반환합니다.

분석 상태는 `analyzed`, `failed`, `confirmed`, `dismissed`입니다. 동기 요청 중에는 아직 기록 조회 대상이 아니므로 프론트의 로컬 loading 상태를 사용합니다.

| API | 동작 |
|---|---|
| `GET /api/equipment/recognitions?status=analyzed` | 분석 기록 목록을 같은 객체 형태로 반환합니다. |
| `GET /api/equipment/recognitions/{recognition_id}` | 분석·후보·등록 상태를 조회합니다. |
| `GET /api/equipment/recognitions/{recognition_id}/image` | 관리자 인증 후 비식별 근거 이미지를 제공합니다. 만료·삭제 시 404를 반환합니다. |
| `POST /api/equipment/recognitions/{recognition_id}/confirm` | 관리자가 후보를 수정·확정하여 장비를 생성하고 장비 객체를 반환합니다. |
| `POST /api/equipment/recognitions/{recognition_id}/dismiss` | 후보를 사용하지 않음으로 처리하고 분석 객체를 반환합니다. |

확정 요청은 다음과 같습니다. `candidate_id`는 해당 분석의 후보 ID 또는 `null`입니다. 관리자가 종류를 직접 수정하거나 후보가 없으면 `null`을 사용합니다. 장비와 PPE는 요청 값으로 등록하며 VLM이 PPE를 자동 결정하지 않습니다.

```json
{
  "candidate_id": "EC_example",
  "name": "프레스 1호기",
  "equipment_type": "press",
  "zone_name": "프레스 구역",
  "required_ppe": ["vest"]
}
```

확정은 DB 트랜잭션 안에서 장비 생성과 기록 연결을 함께 수행합니다. 동일 분석의 중복 확정은 `409 recognition_already_confirmed`로 거절하고 GET의 `equipment_id`로 기존 결과를 확인하도록 합니다. 실패·기각 기록은 확정할 수 없으며 수동 POST 장비 등록을 사용합니다. 기각 요청은 반복해도 성공하지만 확정 기록의 기각은 409로 거절합니다. 재분석은 새 POST 요청으로 진행합니다.

근거 이미지는 초기에는 24시간 뒤 자동 삭제하고, 만료 후 `evidence_url=null`로 반환합니다. 만료된 분석은 확정을 차단하고 새 분석 또는 수동 등록을 안내합니다. 분석·등록 기록은 남기되 이미지를 모델 학습용으로 자동 보관하지 않습니다. 장비 설정용 자료에는 이벤트 최종 검토·학습 보관 동의 절차를 그대로 적용하지 않으며, 기간 연장이 필요하면 별도 목적과 정책을 합의합니다.

이미지 조회는 Bearer 인증 후 fetch로 받은 blob을 표시합니다. HTML img 태그에 Bearer 헤더를 넣을 수 없으므로 원본 공개 경로나 URL query token으로 우회하지 않습니다.

## 3. 카메라 목록과 장비 연결

`GET /api/cameras?zone_name=...`는 실제 `camera_info`를 기준으로 목록을 반환합니다. 이벤트가 없는 카메라도 포함하며 현장 PC 연결 정보를 함께 제공합니다. 기존 `PUT /api/cameras/{camera_id}/node`는 유지합니다.

```json
{
  "items": [
    {
      "camera_id": "CAM_001",
      "camera_location": "프레스 정면",
      "zone_name": "프레스 구역",
      "status": "active",
      "source_node_id": "field01",
      "output_node_id": "field01"
    }
  ]
}
```

카메라 `status`는 `active`, `inactive`, `unknown`으로 반환합니다. 기존 seed의 `active`는 유지하고, 기존 DB에 다른 값이나 null이 있으면 `unknown`으로 정규화합니다. 카메라 등록 상태와 현장 PC online 상태는 다르므로 이 값으로 접속 여부를 판단하지 않습니다. node ID는 미연결 시 `null`로 반환합니다.

`GET /api/cameras/{camera_id}/equipment`와 `PUT /api/cameras/{camera_id}/equipment`를 제공합니다. 초기에는 한 카메라 전체에 적용할 장비만 연결하며, 작업 위치 ID는 도입하지 않습니다.

PUT은 `{ "equipment_ids": ["EQ_example"] }`로 전체 목록을 교체합니다. 같은 구역의 활성 장비만 연결할 수 있으며 중복은 422로 거절합니다. 빈 배열 저장도 명시적으로 설정한 것으로 기록합니다. 조회·저장 응답은 아래와 같습니다.

```json
{
  "camera_id": "CAM_001",
  "scope": "camera",
  "configured": true,
  "equipment_ids": ["EQ_example"],
  "updated_at": "2026-10-06T12:00:00Z"
}
```

한 번도 설정하지 않았으면 `configured=false`, 빈 목록, `updated_at=null`을 반환합니다. 장비가 비활성화되어도 기존 ID는 조회 목록에 남고 적용 PPE에서는 제외합니다. 이후 장비 전체 목록에서 비활성 상태를 확인하고 연결을 해제할 수 있습니다.

## 4. 적용 PPE 미리보기

`GET /api/cameras/{camera_id}/ppe-policy`를 제공합니다. 초기 계산은 구역 기본 PPE와 해당 카메라에 명시적으로 연결된 활성 장비의 추가 PPE를 합집합으로 계산합니다. 같은 구역의 모든 장비를 일괄 합산하지 않습니다.

```json
{
  "camera_id": "CAM_001",
  "zone_name": "프레스 구역",
  "scope": "camera",
  "configured": true,
  "configuration_issues": [],
  "zone_required_ppe": ["helmet"],
  "equipment_requirements": [
    { "equipment_id": "EQ_example", "name": "프레스 1호기", "required_ppe": ["vest"] }
  ],
  "required_ppe": ["helmet", "vest"],
  "detectable_ppe": ["helmet", "vest"],
  "unsupported_ppe": []
}
```

`detectable_ppe`는 현재 연결된 추론 모델이 지원하는 코드 목록이며 초기에는 helmet·vest 기준으로 관리합니다. `unsupported_ppe`는 최종 필수 항목 중 모델이 지원하지 않는 항목입니다. 이 응답만으로 기존 추론·이벤트·방송에 규칙이 적용되었다고 표시하지 않습니다.

구역 관계·구역 규칙·장비 적용 설정 중 누락이 있으면 `configured=false`, `configuration_issues`에 `camera_zone_unassigned`, `zone_rule_missing`, `equipment_scope_unconfigured` 등을 반환하며 최종 `required_ppe=null`로 제공합니다. 확인 가능한 기본값과 장비 정보는 계속 반환합니다. 명시적으로 설정한 빈 규칙은 `configured=true`, `required_ppe=[]`로 반환합니다.

구역 카드에는 장비 목록과 장비별 추가 PPE를 표시합니다. 구역 전체에 적용할 단일 합산값으로 표현하지 않고, 최종 적용 PPE는 카메라별 미리보기에서 표시합니다.

## 5. 구역 및 DB 호환

기존 `GET /api/zones`, `PUT /api/zones/{zone_name}`, 작업자 구역 조회 및 사용자 계정의 `zone_name`은 유지합니다. 초기에는 별도 zone ID를 API에 요구하지 않습니다. 장비는 등록된 구역 규칙과 연결하며, 구역 삭제 시 장비·카메라·사용자 연결이 남아 있으면 409로 거절하도록 기존 검사를 확장합니다.

DB에는 장비, 카메라-장비 연결, 카메라 적용 설정 여부, 분석·후보 기록 및 비식별 근거 이미지 보관 정보를 추가할 예정입니다. 별도 zone 엔티티가 필요해지면 기존 문자열 기반 API와 데이터를 유지하는 마이그레이션을 설계합니다.

## 주요 업무 오류

| HTTP | detail |
|---|---|
| 404 | `equipment_not_found`, `camera_not_found`, `recognition_not_found`, `evidence_unavailable` |
| 409 | `equipment_has_camera_links`, `equipment_zone_mismatch`, `equipment_inactive`, `recognition_already_confirmed`, `recognition_not_confirmable`, `recognition_evidence_expired` |
| 422 | `zone_not_found`, `candidate_not_in_recognition` 또는 기존 형식 검증 오류 |

분석 실패 객체의 `error_code`는 `redaction_failed`, `provider_not_configured`, `provider_timeout`, `provider_unavailable`, `provider_rate_limited`, `provider_response_invalid` 등을 사용합니다. 외부 API 키나 원본 경로·외부 응답 원문을 프론트에 노출하지 않습니다.

## 권장 구현 순서

1. 장비 DB·수동 CRUD와 기존 구역 계약을 먼저 구현합니다.
2. 실제 카메라 목록·현장 PC 연결 조회·장비 매핑을 구현합니다.
3. 적용 PPE 미리보기와 미설정·지원 범위 표시를 구현합니다.
4. 제공업체를 선택하여 VLM 분석·근거 이미지·관리자 확정을 연결합니다.
5. 별도 협의로 실제 추론·이벤트 생성·방송 판단에 정책을 연결합니다.

프론트는 합의된 계약에 따라 mock 화면을 먼저 개발합니다. 프론트 파일 변경·API 구현·테스트 실행·이슈 댓글 등록은 이번 문서 작성에서 수행하지 않습니다.
