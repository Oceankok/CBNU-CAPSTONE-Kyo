# 작업자 비동기 PPE 번역 계약 (#245)

기존 검토형 번역 API/schema34 위에 자동 제공 캐시와 영속 작업을 추가합니다. 구역 CAS schema35를 보존하고 migration36으로 작업·한도 테이블을 추가합니다. 기존 원문·번역·사건·구역 데이터는 재작성하지 않습니다. 자동 캐시는 관리자 검토형 번역과 별개입니다. 이전 계약의 문서별 승인 조건은 기존 수동 게시 API에 계속 적용됩니다.

## 검증 대상과 설정

자동 생성은 기본 OFF이며 저장소에 검증된 언어·문구를 기본 등록하지 않습니다. 현재 데이터의 PPE/provider/model만으로 안전성을 분류하지 않습니다. 운영자가 소유한 JSON 파일을 `PPE_AUTO_TRANSLATION_POLICY` 경로로 지정합니다. **정확한 한국어 title/body/checklist의 canonical JSON SHA256, PPE 종류, 목표 언어**가 검증 목록과 일치해야 합니다. 임의 수정한 본문, 설비 정지·정비·작업 순서 자료를 기본 PPE로 표시하는 분류 API는 없습니다.

`backend.education.auto_translation.digest`는 title/body/checklist만 `ensure_ascii=False, sort_keys=True, separators=(',', ':')` JSON으로 직렬화한 UTF-8의 SHA256입니다. 같은 텍스트/PPE를 가진 다른 자료도 검증 언어 범위에는 해당하지만 작업/캐시는 자료 종류·ID·원문 버전·언어별로 분리합니다. 작은 텍스트 변경도 새로운 digest입니다.

다음 예시는 **OFF이며 검증 목록이 비어 있습니다**. 실제 승인·번역 품질 검증을 뜻하지 않습니다.

```json
{
  "enabled": false, "allow_external_processing": false,
  "daily_requests": 10, "total_requests": 100,
  "total_source_chars": 100000, "max_source_chars": 2000, "max_queued": 20,
  "verified_catalog": []
}
```

실제 검증 후에만 항목에 `content_sha256, ppe_type, language, classification:"basic_ppe", safety_critical:false, representative_validation_passed:true, validation_reference`를 등록합니다. 마지막 필드는 문구·언어 검증 근거의 위치입니다. 서버가 그 근거를 검증한 것으로 주장하지 않으며 목록의 운영자 작성·보존이 신뢰 경계입니다. 파일 누락·형식 오류는 fail closed입니다. 파일 최대64KiB, 목록128개, 지원 태그는 기존 언어/지역 부분집합입니다. 검증된 실제 목록과 운영 활성화 승인은 아직 없습니다.

두 boolean 모두 true이고 기존 번역 공급자가 설정되어야 새 작업을 접수/발송합니다. 기존 `PPE_EDUCATION_TRANSLATION_PROVIDER=openai`, `PPE_EDUCATION_TRANSLATION_MODEL`, `PPE_EDUCATION_TRANSLATION_OPENAI_API_KEY`를 사용합니다. 생성 OFF나 공급자 미설정이어도 **유효한 검증 목록에 남아 있는 현재 캐시**는 조회합니다. 목록 철회·원문 버전/내용 변경·게시 취소 시 자동 캐시는 숨깁니다. 검토 게시본은 자동 생성 OFF와 무관합니다.

## 작업자 조회와 요청

기존 worker 교육/bundle GET은 읽기 전용입니다. 직접 선택 `?language=` → 생략 시 `Accept-Language` → 한국어 순서를 유지합니다. 서버 계정 언어 저장은 추가하지 않습니다. 단일 `material` 문자열과 bundle `title/body/checklist`는 선택 콘텐츠이며 기존 실제/요청 언어와 fallback 필드를 유지합니다. 각 item에 다음을 추가합니다.

```json
{
  "translation": {
    "status": "missing", "origin": "ko",
    "source_kind": "material", "source_id": "MATERIAL_ID", "source_version": 1,
    "available_languages": ["en"], "request_language": "en", "poll_after_seconds": null
  },
  "original": {"title":"한국어 제목","body":"한국어 본문","checklist":["확인 사항"]}
}
```

현재 게시 원문이 없으면 단일 item의 material/translation/original은 null입니다. available_languages는 현재 검토 게시 번역과 정확한 검증 목록 언어이며 캐시 존재를 보장하지 않습니다.

상태는 original/manual/ready/missing/pending/failed/ineligible/disabled입니다. origin은 ko/reviewed/ai입니다. 검토형 번역의 provider가 openai여도 사람이 게시했으면 reviewed입니다. 우선순위는 **허용 후보의 검토 게시본 → AI 캐시 → 한국어**입니다. en-US 요청에 검토 en과 AI en-US가 있으면 검토 en이 우선하며 fallback=true입니다. 후보에서 ko를 만나면 이후 낮은 선호는 사용하지 않습니다.

missing일 때 한국어를 표시하면서 인증된 `POST /api/worker/education-translations/request`:

POST의 language는 `translation.request_language`를 사용합니다. 이는 현재 허용된 후보 중 실제 검증된 생성 언어이며, 최우선 requested_language와 다를 수 있습니다(브라우저 fr,en에서 en만 검증된 경우). 원문/비활성/부적격/이미 제공 가능한 결과는 request_language:null입니다. locale 전환 시 이 값도 이전 응답과 함께 폐기합니다.

```json
{"source_kind":"material","source_id":"MATERIAL_ID","source_version":1,"language":"en-US"}
```

202 `{status:"pending",poll_after_seconds:3}`입니다. 이미 제공 가능한 결과는202와 ready/manual 및 null poll입니다. en-US 자체가 미검증이어도 후보 en이 검증되었으면 en 작업을 사용합니다. 같은 종류·자료·버전·실제 생성 언어는 한 작업만 저장합니다. API는 본문을 받거나 즉시 외부 호출하지 않습니다.

401/403 인증/역할, 404 현재 본인에게 노출되지 않는 원문, 409 education_translation_source_changed 버전 변경입니다. disabled/ineligible/failed는 education_auto_translation_<상태> 409, 큐가 가득 차면429입니다. GET/POST마다 현재 배정 구역·최신 분기·주제·원문 게시를 확인합니다. 다른 구역 원문/통계·내부 job key는 반환하지 않습니다. 기존 worker private/no-store 및 Vary를 유지합니다.

pending은 같은 GET을 최소3초 간격으로 조회합니다. 큐60초·발송120초 뒤에는 failed 표시로 종료하며 worker가 없어도 무한 poll하지 않습니다. 실패/409는 자동 POST 재시도하지 않습니다. 화면/언어/원문 전환 뒤 이전 결과는 적용하지 않습니다. AI 캐시는 AI 표시와 반환된 한국어 원문 보기를 제공합니다.

## 관리자 수정

기존 CRUD/승인/버전 API를 유지합니다. 추가 관리자 GET은 `/api/education/materials/{id}/automatic-translations?source_version=N` 및 bundles/{id}/automatic-translations입니다. `{items:[{source_kind,source_id,source_version,language,status,origin:"ai",content}]}`이며 현재 게시 원문의 버전/digest와 일치하는 ready만 content(title/body/checklist)를 제공합니다. 생성 OFF에도 현재 캐시를 복사할 수 있습니다. 이전 버전409, 미게시409, 없음404입니다. job ID는 반환하지 않습니다. content를 **기존 수동 번역 POST로 복사**해 정상 초안 ID를 만들고 수정·명시 게시합니다. 자동 job을 번역 ID로 PATCH하지 않습니다.

## 실행·비용·복구

native 서비스는 동일 DB/schema·정책·공급자 환경에서 `python -m backend.education.auto_translation`을 별도 프로세스로 실행합니다. --once는 한 큐 항목 처리 후 종료하는 검사/단발 실행용입니다. 자동 DB 초기화는 하지 않습니다. 정책/공급자 설정 전에는 발송하지 않습니다.

Compose overlay와 native worker는 **별도로 관리하는 opt-in 후보**입니다. `deploy/Release.ps1`은 compose.release.yaml과 Caddyfile만 release 디렉터리에 복사하며, 일반 업그레이드/rollback에 compose.translation.yaml 또는 translation-worker를 포함하지 않습니다. overlay를 준비했다는 사실이 기존 배포 도구에 통합됐거나 운영 준비가 검증됐다는 뜻은 아닙니다.

별도 운영자가 overlay 경로와 PPE_AUTO_TRANSLATION_POLICY_FILE, **기존 배포와 같은 project-name/env-file, API 이미지 digest와 DB/schema36**을 명시적으로 맞춰야 합니다. 예시 형식은 `docker compose --project-name <기존-project> --env-file <기존-env> -f <배포-compose.release.yaml> -f <별도-compose.translation.yaml> --profile translation up -d api translation-worker`입니다. 각 꺾쇠 항목은 실제 경로/이름으로 대체해야 합니다. 다른 project로 실행해 서비스/DB를 중복 생성하거나, 이미지/정책을 일반 Release.ps1 rollback과 독립적으로 방치하지 않아야 합니다. worker 시작·중단·버전 변경·rollback은 이 후보를 사용하는 운영자의 별도 관리 대상입니다. 이 구현은 release-script를 변경하거나 운영 명령/활성화를 실행하지 않습니다. 실제 runtime 결합 검증은 격리 native worker로 수행하며 일반 배포 lifecycle 통합은 별도 범위입니다.

DB의 schema advisory lock(PG)/write lock(SQLite)이 큐·한도·claim을 직렬화합니다. queued→dispatched와 총/UTC일별 요청 수·누적 원문 문자 수를 **외부 I/O 이전 commit**합니다. 고유 claim token/120초 deadline이 있으며 다른 프로세스가 재claim하지 않습니다. 원문/검증목록/현재 검토 게시본도 발송 전에 재확인합니다. 큐에 있는 동안 수동 게시가 생기면 호출/한도 소비 없이 제외합니다. 발송 후 수동 게시가 생기면 비용을 취소할 수 없지만 조회는 수동이 우선합니다.

외부에는 한국어 title/body/checklist와 목표 언어만 전달합니다. 구역·카메라·사용자·사건·추천·snapshot을 붙이지 않습니다. 원문에 직접 적힌 비공개 정보는 전송될 수 있으므로 정확한 검증 텍스트 자체를 확인해야 합니다. 최대 원문 문자/총·일별 요청/누적 원문 문자/큐 수를 제한하고 공급자는 기존 max_output_tokens=8000과 응답/내용 검증을 유지합니다. 이는 금액 기반 실시간 청구 상한이 아닙니다.

공급자는 idempotency 보장 없는 단일 httpx POST입니다. **애플리케이션 작업별 최대1회 발송 시도**만 보장하며 공급자 정확히1회 청구를 보장하지 않습니다. fence commit 뒤 중단되면 HTTP를 안 보냈어도 재발송하지 않습니다. timeout/실패/결과 불명확은 재발송·한도 환급하지 않습니다. 원문 버전을 인위적으로 바꿔 재요청하지 않습니다. DB commit이 불명확하면 외부 호출하지 않습니다. 실패 시 한국어와 수동 번역이 복구 경로입니다.

## 검사

backend.tests.test_auto_translation은 합성 정책이며 사람 승인으로 간주하지 않습니다. 동시POST·독립CLI3개/mock 발송1회, 캐시/수동 지역 우선, 원문 변경/취소/목록 철회, OFF 캐시 유지, 미설정/실패/불명확/늦은 결과, worker부재 만료, quota·schema35→36 보존, 권한/본문 전송 경계, 관리자 복사와 bundle를 검사합니다. 실제 유료 호출·언어 품질·운영 활성화는 포함하지 않습니다.

2026-11-15 격리 검사 명령:

```powershell
python -W ignore -X utf8 -m unittest backend.tests.test_auto_translation backend.tests.test_education_translations backend.tests.test_education_bundle_translations backend.tests.test_education_languages backend.tests.test_worker_education backend.tests.test_education_upgrade backend.tests.test_postgres_initialization_order backend.tests.test_education_stats_refresh backend.tests.test_openapi_contract -v
```

- 기준은 zone260의 2c6f3c4ec236bef18a161603a05c133766b8beea 위 작업입니다(원래 main562b2df, schema35 의존).
- 임시 SQLite:85회 중84통과/PG전용1skip,34.924초. PostgreSQL 전용 kyo_test_translation_auto_20261115/UUID schema:85회 중79통과/역사 SQLite전용6skip,79.223초. 기존 locale 테스트의 상속/중복 수집을 포함한 실행 횟수이며 고유 시나리오 수로 주장하지 않습니다. 신규 자동 번역17개는 두 DB 모두 통과했습니다.
- 신규 실제 CLI 검사3프로세스가 같은 DB의 API 생성 작업에 접근하고, mock 공급자 발송 로그1줄과 quota1회/ready를 확인했습니다. API는 FastAPI TestClient이며 네트워크 API/브라우저 결합은 별도 독립 검증 범위입니다.
- Compose overlay `docker compose ... config --quiet`, 변경 Python compile, git diff --check 통과입니다. Compose 서비스를 실행한 결과는 아닙니다.
- 로그는 전용 worktree의 무시되는 `.private_media/translation-validation/sqlite-final.log`, `postgres-final.log`입니다. 전용 PG의 test schema 잔여0개를 확인했습니다. 최초 PG 실행은 기존 이관 fixture의 final35 고정값 때문에1실패했고 LATEST_VERSION으로 수정한 후 위 최종 실행이 통과했습니다.
- 제품 커밋 a3061167b2471776777b919e2f328d2a5ecdd7c3의 CI43688148159는6job 모두 성공했습니다. backend783회 중772통과/11skip, PG783회 중770통과/13skip이며 각 job의 inference343회 중272통과/71skip입니다. 영상 환경 관련 skip을 실제 영상 전체 검증 완료로 해석하지 않습니다. 이후 runbook 보완은 문서만 변경하며 위 제품 검사 기준을 유지합니다.
