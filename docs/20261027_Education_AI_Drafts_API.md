# OpenAI 교육 자료 초안 생성 (#111)

추천 주제의 한국어 교육 본문·체크리스트를 OpenAI API로 생성하고 기존 `education_material`의 **초안**으로 저장합니다. 관리자 수정·승인·게시와 작업자 `material: string | null` 계약은 그대로 사용합니다. 프론트 화면은 변경하지 않습니다.

## 관리자 API 계약

두 API 모두 관리자 Bearer 인증이 필요합니다. 미인증은 401, 작업자는 403입니다.

### 설정 상태 조회

`GET /api/education/generation`

기본 응답은 다음과 같습니다. 조회만으로 외부 API를 호출하지 않습니다.

```json
{"configured": false, "provider": null, "model": null, "reason": "education_generation_disabled"}
```

서버에 공급업체·키·모델이 설정되면 `configured=true`, `provider="openai"`, 설정한 `model`, `reason=null`을 반환합니다. API 키는 응답하지 않습니다. `configured`는 로컬 설정 여부이며 실제 계정 잔액·키 유효성·모델 접근 가능성을 검사한 결과는 아닙니다.

### 추천별 AI 초안 생성

`POST /api/recommendations/{recommendation_id}/materials/generate`

요청 본문은 필요하지 않습니다. 서버의 추천 주제·PPE 코드를 사용하며 클라이언트에서 모델·프롬프트·작성자·게시 상태를 지정하지 않습니다. 성공하면 **201**과 기존 관리자 자료 조회 형식의 응답을 반환합니다.

```json
{
  "material_id": "MAT_...",
  "recommendation_id": "REC_...",
  "status": "draft",
  "provider": "openai",
  "model": "실제 응답 모델 ID",
  "title": "생성된 제목",
  "body": "생성된 교육 본문",
  "checklist": ["확인 항목"],
  "created_by": "인증한 관리자 ID",
  "version": 0
}
```

위 예시는 일부 필드만 표시합니다. 분기·구역·PPE·주제와 시간 등 기존 필드도 포함됩니다. 생성된 초안은 기존 목록에 나타납니다. 관리자가 기존 PATCH로 내용을 수정하고 기존 publish API에 `approved=true`와 현재 버전을 보내야 작업자에게 노출됩니다. 새 AI 초안 생성만으로 기존 게시 자료를 교체하지 않습니다.

## 프론트 연동

1. 관리자 교육 화면에서 설정 상태를 조회합니다. 비활성이거나 설정이 없으면 AI 생성 버튼을 비활성화하고 기존 수동 작성을 유지합니다.
2. 추천 카드의 AI 생성 요청 동안 버튼을 비활성화합니다. HTTP 요청 완료까지 기다리며 자동 재시도하지 않습니다.
3. 201 응답을 기존 초안 편집 영역에 표시하고 관리자 수정·검토·게시 흐름을 사용합니다.
4. 실패 시 오류 코드를 안내하고 수동 작성을 유지합니다. API 키를 프론트 입력·저장·번들 환경변수에 넣지 않습니다.

자동 번역·자동 게시·장비 인식·영상 추론은 이 API에 포함하지 않습니다. 생성 본문은 텍스트로 표시하고 임의 HTML로 실행하지 않습니다.

## 오류와 동시 요청

| HTTP | detail | 의미 |
| --- | --- | --- |
| 503 | `education_generation_disabled` | 기본 비활성 상태입니다. |
| 503 | `education_generation_not_configured` | 공급업체·키·모델 설정이 없거나 잘못됐습니다. |
| 503 | `education_provider_auth_failed` | 공급업체 키 또는 접근 권한 문제입니다. 프로젝트 관리자 로그인 실패와 구분합니다. |
| 429 | `education_generation_busy` | 같은 API 프로세스에서 다른 생성이 진행 중입니다. |
| 429 | `education_provider_rate_limited` | 공급업체의 요청·사용 한도에 걸렸습니다. |
| 504 | `education_provider_timeout` | 공급업체 응답 대기 시간이 초과됐습니다. |
| 502 | `education_provider_unavailable` | 네트워크 또는 공급업체 요청 실패입니다. |
| 502 | `education_provider_response_invalid` | 미완료·손상·길이 초과 등 응답 검증 실패입니다. |
| 422 | `education_generation_refused` | 공급업체가 생성을 거절했습니다. |
| 404 | `recommendation_not_found` | 생성 시작 시 추천을 찾을 수 없습니다. |
| 409 | `education_recommendation_changed` | 응답 대기 중 추천이 삭제되거나 범위·주제가 바뀌었습니다. 저장하지 않습니다. |
| 409 | `education_topic_invalid` | 추천 주제가 비어 있거나 2,000자를 초과합니다. |

실패하면 새 자료를 저장하지 않으며 기존 초안·게시 자료를 유지합니다. DB 쓰기 잠금을 잡은 채 외부 응답을 기다리지 않습니다. 저장 시 추천 분기·구역·PPE·주제를 다시 비교합니다. 프로세스별 생성 한도는 1건이며 분산 작업 큐·예약 생성·복잡한 중복 과금 방지 시스템은 추가하지 않습니다. 같은 요청을 다시 보내면 새 API 호출·비용이 발생할 수 있으므로 확인 없이 재시도하지 않습니다.

## 서버 설정과 비용

기본값은 모두 비어 있습니다. 실제 사용을 결정했을 때만 서버의 보호된 환경 설정에 다음을 채웁니다.

```dotenv
PPE_EDUCATION_PROVIDER=openai
PPE_EDUCATION_MODEL=
PPE_EDUCATION_OPENAI_API_KEY=
```

모델은 Responses API와 strict JSON Schema를 지원하고 해당 API 프로젝트에서 접근 가능한 모델 ID로 직접 설정합니다. 코드가 임의의 모델이나 기존 장비 인식용 ChatGPT 구독 자격 증명을 선택하지 않습니다. 일반 `OPENAI_API_KEY`도 자동 재사용하지 않습니다. 키·모델이 비어 있으면 외부 호출을 거절합니다. ChatGPT 구독과 별도인 API 과금이 발생하므로 실제 호출 전에 모델 가격·프로젝트 사용 한도를 확인합니다.

Compose에서는 **api 서비스에만** 위 변수를 전달합니다. db-init·maintenance·GPU worker·web에는 교육 API 키를 전달하지 않습니다. 설정 변경은 승인된 배포·API 재시작 절차로 반영하며, 현재 실행 서버의 설정을 이 코드 변경으로 자동 수정하지 않습니다. 새 SDK 의존성 없이 기존 httpx를 사용합니다.

외부 요청은 고정된 `https://api.openai.com/v1/responses`로 보냅니다. redirects·환경 프록시와 자동 재시도를 사용하지 않습니다. 연결 시간 제한은 5초, 읽기 대기는 30초이며 출력 토큰 상한은 2,400입니다. 토큰 한도 등으로 미완료된 응답은 초안으로 저장하지 않습니다. `store=false`를 요청하며 이것을 별도의 전체 데이터 보관 면제 보장으로 설명하지 않습니다.

전송 데이터는 추천의 `education_topic`과 `ppe_type`뿐입니다. 주제 문자열 자체에 구역명이 들어 있을 수 있으므로 외부 전송이 가능한 명칭을 사용합니다. 사용자 ID·원본 이벤트·위반 건수·추천 점수·이미지·영상·동의서 파일은 보내지 않습니다. 프롬프트에는 현장 안전지침·제조사 안내 우선과 미확인 법규·수치·기계 조작 절차를 만들어내지 않도록 요청합니다. 구조 검증만으로 내용의 정확성이 보장되지는 않으므로 기존 관리자 검토를 반드시 거칩니다.

## DB·검증 범위

기존 v14의 `education_material.provider`·`model`을 사용하므로 새 테이블·스키마 이관은 필요하지 않습니다. 수동 작성은 기존처럼 `provider=manual`입니다. AI 초안의 수정 후에도 생성 출처는 보존하고 기존 수정 담당자·시간·버전을 갱신합니다.

테스트는 가짜 OpenAI 응답과 임시 DB로 권한·비활성·전송 범위·미승인 비노출·승인 게시·공급업체 오류·거절·비정상 응답·추천 변경·동시 생성 제한을 확인하도록 작성했습니다. 실제 API 키 설정·유료 호출·교육 내용 품질 평가는 별도이며 이번 구현 단계에서 실행하지 않습니다.

공식 참고: [Responses API](https://developers.openai.com/api/docs/guides/text), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
