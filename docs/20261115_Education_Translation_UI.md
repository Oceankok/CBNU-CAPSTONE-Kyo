# 교육 번역 UI 검토 및 검사 기록

Refs #245. 기준 커밋은 `562b2dffdab6f22cf18aff16185499f2f418e55f`이며, 기존 교육 편집 및 구역 입력 보존 변경 위에서 별도 프론트 브랜치로 작성했습니다.

## 구현

작업자 화면의 교육자료 언어 선택은 사용자별 명시 선택을 우선하며, 선택하지 않으면 언어 쿼리를 생략하여 서버가 브라우저 `Accept-Language`를 처리합니다. 사용할 번역이 없으면 서버가 제공한 한국어 원문을 표시합니다. 실제 언어와 요청 언어가 다르면 두 언어를 함께 표시합니다. AI 자동 번역, 수동/검토 게시 번역, 한국어 원문을 서버의 출처 필드로 구분하고 번역 옆에서 한국어 원문을 펼쳐 볼 수 있습니다. 구역 안전 수칙 자체는 번역하지 않습니다.

GET은 읽기 전용입니다. 서버가 `missing`으로 판정한 문서에만 원본 종류·ID·버전·요청 언어를 담은 enqueue POST를 보냅니다. 클라이언트에서 문구를 보고 자동 번역 적격성을 추정하거나 승인하지 않습니다. 서버의 검증된 정확한 원문/언어 카탈로그가 자동 생성 범위를 결정합니다. 생성 언어는 요청 언어 표시 필드가 아니라 서버의 `translation.request_language`만 사용합니다. 브라우저 우선 언어 fr에 번역이 없고 다음 후보 en이 검증된 경우에도 en을 정확히 요청합니다. 캐시된 번역과 수동 번역은 응답 즉시 표시합니다. 서버의 최종 실패 작업은 자동 재요청하지 않습니다. enqueue 전송 실패는 한국어 본문과 실패 안내를 유지하고 별도 명시 재시도를 제공합니다. POST 응답을 잃었어도 다음 GET이 pending을 반환하면 다시 enqueue하지 않고 조회를 이어갑니다. 서버의 영속 중복 방지 계약을 사용하며 외부 제공자를 직접 호출하지 않습니다.

준비 중에는 최소 3초 간격으로 상태를 확인합니다. 준비가 끝난 문서도 활성 페이지에서 30초마다 최신 게시 상태를 다시 읽습니다. 개별 요청의 대기 한도는 10초이고, 마지막 성공 응답으로부터 40초 동안 최신성을 확인하지 못하면 표시 본문을 숨깁니다. 창 포커스 복귀/탭이 다시 보일 때에는 기존 본문을 숨기고 즉시 조회합니다. 이는 즉시 원격 회수를 보장하지 않으며, 백그라운드 브라우저의 타이머 제한과 오프라인에서는 재조회까지 본문을 사용할 수 없도록 처리합니다. 최신 응답에서 원본이 사라지거나 게시 취소되면 번역과 원문 토글도 제거합니다.

관리자 단일·통합 자료에는 공통 수동 번역 패널을 연결했습니다. 기존 원본 검토·게시 흐름과 별도로 번역 초안을 작성·수정·명시 게시/취소할 수 있습니다. 기존 검토용 번역의 provider가 openai여도 검토 게시 자료로 취급합니다. 자동 작업의 출처와 provider 필드를 혼동하지 않습니다. 자동 캐시 결과의 별도 읽기 API는 현재 원본 종류·ID·버전과 일치하는 ready 내용만 복사 대상으로 허용합니다. 복사 후에는 일반 수동 번역 POST로 새 초안을 만들며 자동 작업 ID를 편집하지 않습니다.

저장 중에는 입력과 전환을 잠그고 즉시 적용되는 요청 잠금으로 같은 이벤트 구간의 중복 생성을 방지합니다. 초기/명시/저장 후 목록 조회는 공통 요청 순서 검사로 오래된 응답을 버립니다. 번역 버전 충돌은 입력을 보존하고 최신 내용을 검토·선택한 뒤 별도로 저장합니다. 원본 변경/게시 취소 충돌은 원본 상세를 다시 조회해 제목·본문·체크리스트·버전·상태를 보여 줍니다. 원본이 게시되어 있을 때만 관리자가 입력을 유지하여 최신 원본의 **새 번역 초안**으로 명시 전환할 수 있습니다. 게시 취소 원본은 저장을 막고 원본 게시 후 재조회할 경로를 제공합니다.

## Wire 계약과 의존성

`WorkerTranslationFields`는 기존 선택 콘텐츠 및 `language`, `requested_language`, `translation_fallback`에 다음 응답을 추가합니다.

- `translation`: status(original/manual/ready/missing/pending/failed/ineligible/disabled), origin(ko/reviewed/ai), source_kind, source_id, source_version, available_languages, request_language, poll_after_seconds. 현재 원본이 없으면 null입니다.
- `original`: 한국어 title/body/checklist 또는 null입니다.
- POST `/api/worker/education-translations/request`는 종류/ID/버전/언어를 받아 현재 상태와 poll_after_seconds를 반환합니다.
- 관리자 GET `/api/education/{materials|bundles}/{id}/automatic-translations?source_version=N`은 자동 작업 ID 없이 종류/ID/버전/언어/status/origin/content를 반환합니다. 현재 게시 원본의 ready 결과만 content를 제공합니다.

서버는 허용된 언어 후보 내의 최신 검토 게시 번역을 AI 캐시보다 우선합니다. 예를 들어 요청 en-US에 대한 수동 en은 자동 en-US보다 우선하며 실제/요청 언어 필드가 fallback을 설명합니다. 백엔드 담당의 별도 async 작업·카탈로그·예산·schema36 변경이 필요한 기능입니다. 이 프론트 변경에는 backend, migration, ZoneRulesPage 또는 구역 검사를 포함하지 않습니다.

## 검증

2026년 11월 15일, 실제 React/React DOM/교육 API client를 Edge headless에서 실행하고 HTTP만 격리한 브라우저 검사로 확인합니다. 테스트 서버는 환경 파일과 운영 Vite proxy를 사용하지 않습니다. 새 번역 검사는 5188, 기존 교육 회귀는 5187을 사용합니다. JSON 결과는 각각 `frontend/test-results/translations.json`, `education.json`입니다.

번역 29개 시나리오는 단일/통합 수동 생성·저장 잠금·명시 게시, 번역409, 원본 변경/게시 취소, 언어 우선순위·저장, missing→pending→ready/failed, 수동 우선 응답, 늦은 언어 응답, 원본 무효화, disabled/ineligible/failed 무생성, 게시409, 자동 캐시 복사, 검토용 AI 작성 초안, 지역 fallback, 통합 작업자 표시, 초기 목록 경쟁/중복 생성 잠금, source409의 구체 복구, 자동 최신성 갱신·만료, enqueue 실패/응답 유실, 원본 버전 전환 뒤 늦은 목록 응답을 포함합니다. 오래된 응답 및 원격 변경 검사는 지연 응답/가상 브라우저 시계를 사용합니다.

```powershell
cd frontend
npm ci
$env:PLAYWRIGHT_CHANNEL = 'msedge'
npm run test:translations:browser
npm run test:education:browser
npm run lint
npm run build
```

Linux CI는 기존 Chromium 설치 후 두 브라우저 명령을 실행하고 `education-browser-results` artifact에 JSON과 실패 trace를 수집합니다. `PLAYWRIGHT_CHANNEL`은 설정하지 않습니다. 검사별 최종 결과와 소스 SHA256은 `docs/translation-ui-20261115/results.json`에 기록합니다.

실제 서버/DB와의 결합, 다중 관리자 경합 및 실제 언어 품질은 별도 통합 검증 대상입니다. 유료 번역 제공자는 호출하지 않았고 자동 생성 운영 설정·검증 카탈로그를 활성화하지 않았습니다. 배포와 원격 main 병합은 수행하지 않습니다.
