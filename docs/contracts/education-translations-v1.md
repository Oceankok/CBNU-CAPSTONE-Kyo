# 교육자료 번역 API 계약 (v1)

상태: 로컬 구현 및 격리 테스트. 배포/유료 번역 호출은 하지 않았음.
대상은 **단일 `education_material` 및 다중 구역 `education_bundle` 게시본**이다. 계정의 언어 선호 저장은 후속 범위다.

## 원칙
- 원문은 한국어이며 수정하지 않는다. 번역은 별도 행으로 원문의 `material_id` 또는 `bundle_id` + `source_version`에 고정된다. DB CHECK로 원문을 정확히 하나만 연결하며 두 종류 모두 FK를 적용한다.
- 원문이 게시 상태여야 번역 초안 작성/수정/게시 가능. 원문 버전 또는 게시 상태 변경 시 기존 번역은 `stale=true`로 표시하고 작업자에게 노출하지 않는다.
- 수동 작성 또는 자동 생성 모두 초안으로 저장. 언어별 관리자 검토 및 `approved=true` 게시가 필수다. 자동 생성 결과를 안전 검증 완료로 취급하지 않는다.
- 대상 언어는 소문자 ISO 언어 코드 2~3자 + 선택 대문자 지역 2자 형태(예: en, vi, zh-CN). ko와 ko-XX는 원문이므로 거절. 언어 존재/품질 자체를 서버가 보증하는 목록은 아니다.

## 관리자 API (모두 admin 권한)

기본 경로 `/api/education`.

| 메서드/경로 | 요청 | 반환 |
|---|---|---|
| GET `/materials/{id}/translations?limit=50&offset=0` | limit 1~100 | `{items:[...]}` |
| POST `/materials/{id}/translations` | language, expected_source_version, title, body, checklist | 201 초안 |
| POST `/materials/{id}/translations/generate` | language, expected_source_version, allow_external_processing:true | 201 초안 |
| GET `/translations/{id}` | 없음 | 번역 객체 |
| PATCH `/translations/{id}` | title, body, checklist, expected_version | 수정된 초안 |
| POST `/translations/{id}/publish` | expected_version, approved:true | 게시된 객체 |
| POST `/translations/{id}/unpublish` | expected_version | 초안 상태로 회수 |
| GET `/translation-generation` | 없음 | configured, provider, model, reason |

반환 객체: translation_id, material_id(nullable), bundle_id(nullable), source_kind(material/bundle), language, source_version, title, body, checklist, status(draft/published/superseded), provider, model, version, created_by/at, updated_by/at, published_by/at, stale.
`title` 1~200자, `body` 1~12000자, checklist 1~20개(각1~500자). 공백 trim, 알 수 없는 요청 필드는 거절.
각 수정 요청의 expected_version은 번역 객체 버전이다. expected_source_version은 게시된 원문 버전이다.
같은 원문+언어 게시본은 하나이며 새 게시 시 기존 게시본은 superseded 처리.
409 원문 변경/게시 해제/낙관적 버전 충돌 시 다시 조회 후 사용자에게 변경 내용을 보여준다. 자동 재시도하지 않는다.

## 작업자 연동
`GET /api/worker/education?language=en`. 명시 query가 최우선이며 생략하면 `Accept-Language`를 사용한다. 헤더도 없거나 유효한 선호 언어가 없으면 ko.
기존 `items`, `groups`, `material` 문자열 형식은 유지. 본인 구역 추천의 현재 게시 원문에 일치하는 **검토·게시된 해당 언어 번역**만 선택한다.
번역 없음/초안/오래된 번역이면 한국어 원문으로 fallback. 원문 게시본 자체가 없으면 기존과 동일하게 material:null.
각 item에 `language`(실제 선택 언어), `requested_language`(최우선 요청), `translation_fallback`(요청과 실제 언어가 다름)을 추가한다. 원문이 없으면 language:null, translation_fallback:false이며 material:null을 유지한다. 배지는 실제 language 기준으로 표시한다.
worker 응답에 번역자 ID/통계/초안은 추가하지 않는다. 계정 언어 저장은 구현하지 않았고 프론트가 선택 언어를 query로 전달한다.

## 프론트 협조 요청
1. 게시 원문 상세에 언어별 번역 목록과 draft/published/superseded/stale 상태.
2. 원문과 번역을 나란히 검토하는 편집 화면, 명시적인 검토 후 게시 및 게시 해제 버튼.
3. 409를 충돌로 처리하고 원문/번역 재조회. 기존 폼의 오래된 내용을 조용히 덮어쓰지 않기.
4. 자동 번역 버튼은 availability.configured일 때만 활성화. 외부 처리 및 과금 가능 안내 후 한 번의 명시 요청으로 생성; polling/자동 재시도/페이지 열 때 생성 금지.
5. worker 언어 선택 query 및 한국어 fallback 안내. 통합자료 상세에서도 동일 번역 검토 UI를 사용하고 번역 생성/목록 경로만 bundle용으로 전환.

## 외부 호출 경계
기본 꺼짐. 아래 환경을 운영자가 별도로 설정해야 한다:
- PPE_EDUCATION_TRANSLATION_PROVIDER=openai
- PPE_EDUCATION_TRANSLATION_MODEL=명시 모델
- PPE_EDUCATION_TRANSLATION_OPENAI_API_KEY=전용 키

기존 교육 생성 설정은 재사용하지 않는다. 명시 POST + allow_external_processing:true가 있어야 외부 호출한다.
전송 대상은 OpenAI Responses API이며 한국어 title/body/checklist와 목표 언어만 전송한다. 자유 텍스트에 개인정보가 들어 있을 수 있으므로 관리자 검토가 필요하다. 카메라 영상/이벤트/계정 ID는 자동 포함하지 않는다.
store:false, 고정 HTTPS endpoint, redirect/proxy/retry 없음, 출력 최대8000토큰, 프로세스별 동시1개. **분산 rate limit/월 예산/요청 멱등성은 아직 없음**. 중복 요청은 중복 과금 가능하므로 자동 재시도 금지. 유료 호출과 결과 품질은 모의 테스트만 수행했다.
외부 호출 후 원문 버전을 다시 확인하며 바뀐 원문의 결과는 저장하지 않고409. 이 경우에도 외부 비용은 발생했을 수 있다.

## 마이그레이션/통합
별도 education_translation 테이블. SQLite/PostgreSQL migration34. 기존 migration32는 bundle, migration33은 자동확정 설정으로 보존한다.
통합 후보는 31→32→33→34 순차 초기화한다. 기존32 게시 bundle, 기존33 자동확정 설정·이력, 기존34 번역 행 보존을 격리 회귀로 확인한다. 이전 단독 번역 prototype33 데이터베이스는 자동 이관하지 않고 lineage 차단을 유지하며 별도 명시적 조정이 필요하다. 운영 데이터베이스에 적용하지 않았다.
공용 수정 파일: backend/api/main.py, backend/db/migrations.py, backend/db/postgres.py, backend/education/service.py, backend/zones/routes.py.
실제 PostgreSQL 테스트는 전용 kyo_test DB가 있을 때만 실행. 프로덕션 DB/서버에 적용하지 않았다.


## 다중구역 자료 번역 연동
- GET/POST `/api/education/bundles/{bundle_id}/translations`, POST `.../translations/generate`를 추가한다. 요청/응답 및 공통 `/translations/{translation_id}` 수정·게시·게시해제 계약은 단일 자료와 같다.
- 번역 원문은 이미 게시·검토된 bundle의 title/body/checklist다. sources_json/구역 목록/통계/작성자 ID를 외부 payload에 붙이지 않는다. 단, 관리자가 작성한 본문 자체에 구역명이나 민감 정보가 있을 수 있으므로 **원문 전체 텍스트가 OpenAI로 전송됨**을 안내하고 확인해야 한다.
- GET `/api/worker/education-bundles?language=en`는 기존 구역·분기·주제 가시성 필터를 먼저 적용한 후, 해당 bundle의 현재 version에 맞는 게시 번역만 title/body/checklist에 덧씌운다. 필터를 번역으로 우회할 수 없다.
- bundle worker item에는 `language`(실제 반환 언어), `requested_language`, `translation_fallback`이 추가된다. en-US는 en-US 게시본을 우선하고 없으면 en 게시본을 선택한다. 지원하는 형식은 기존 2~3글자 언어와 선택적 2글자 지역이며 기존 저장·query 계약의 한계로 스크립트 태그(zh-Hant, zh-Hant-TW 등)는 이번 범위에서 제외한다. 헤더에서는 무시하고 명시 query에서는 422를 유지한다.
- 원문 게시 해제/교체/편집으로 version이 달라지면 기존 번역은 stale이며 재검토할 새 번역이 필요하다. 원문의 snapshot/통계/구역멤버십 정책 자체는 번역 API가 바꾸지 않는다.
- bundle 원문은 여러 구역을 함께 설명하는 하나의 공유 콘텐츠이므로 번역도 동일한 공유 범위다. 본문에서 구역별 문장을 잘라내는 ACL은 제공하지 않는다. 민감한 구역 전용 내용은 단일 자료로 작성해야 한다.

## 독립 감사 반영 및 남은 검증
- 이전 감사에서 SQLite 권한/stale 결함은 발견되지 않았다. 통합 이후 원문 유형 FK, 32→34 보존, bundle 가시성/원문 수정/생성중변경 테스트를 추가했다.
- bundle 생성 provider에는 실제 저장된 주제를 보내지 않고 PPE allowlist의 고정 일반 주제만 보내는 별도 감사 수정본을 포함했다. 번역은 목적상 실제 원문을 보내므로 그 사실을 숨기지 않는다.
- 실제 PostgreSQL 이관·동시게시 경쟁은 이 환경에 PG 서비스가 없어 아직 실행하지 못했다. 배포 전 전용 kyo_test DB에서 같은 테스트를 실행해야 한다.
- 자동 번역은 per-process 동시1개뿐이고 분산 비용 제한·중복 과금 방지는 없다. 아직 live enable 권고 단계가 아니다.

## 언어 협상 보완
- 명시 query의 기존 검증/422 동작을 유지한다. 헤더는 대소문자 정규화, q 가중치 내림차순, 동률 입력 순서로 처리한다. 잘못된 태그/q와 wildcard는 무시한다. 입력은 4096자/32항목으로 제한한다.
- 각 선호 언어의 지역→기본 언어 순으로 검토·게시된 현재 버전 번역만 찾는다. q=0 언어는 헤더 후보에서 제외한다. Korean 후보를 만나면 원문을 택하며, 번역을 찾지 못한 경우에도 기존 교육 접근성을 위해 한국어 원문을 제공한다(406 반환 안 함).
- 조회는 유료 생성/게시/언어 저장을 수행하지 않는다. 권한과 원문 버전/stale 검사는 기존과 같다.
- `translation_fallback`은 지역→기본 언어 변경도 true이다. 원문 수정으로 번역이 무효화되면 기존과 동일하게 ko로 돌아간다.

### 캐시와 허용 언어 없음
- 작업자 응답은 사용자별 자료이므로 `Cache-Control: private, no-store`를 설정하고 `Vary: Accept-Language`를 기존 Vary 값과 합친다. CORS의 `Origin`도 보존한다.
- `en;q=0,ko;q=0`처럼 사용 가능한 선호가 전혀 없는 요청은 엄격한 HTTP 표현 협상 성공이 아니다. 기존 교육 접근성을 유지하는 제품 정책으로 한국어 원문을 반환한다. 406을 반환하지 않으며, 이때 requested_language:ko는 서버 기본 선택을 뜻한다. translation_fallback:false 또한 사용자가 ko를 허용했다는 뜻이 아니다. 해당 필드는 선택된 기본값과 실제 콘텐츠 언어의 차이만 나타낸다.

통합 후보 기준: #248 + #251의 schema33 자동확정 설정을 보존하고 번역은 schema34로 순차 이관한다. 기존 #246 단독 schema33 데이터베이스를 자동 호환한다고 간주하지 않으며 기존 lineage 차단을 유지한다.
