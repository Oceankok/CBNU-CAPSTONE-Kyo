# 인증 및 현장 방송 개발 가이드

## 인증 #93

Python 3.12에서 `python -m pip install -r backend/requirements.txt`로 의존성을 준비한다.
SQLite는 계속 사용한다. `init_db.py`는 기존 테이블/행을 삭제하지 않고 사용자 테이블을 추가한다.

```powershell
python backend/db/init_db.py
python -m backend.auth.manage_users admin01 --role admin --name 관리자
python -m backend.auth.manage_users worker01 --role worker --name 작업자
$env:PPE_JWT_SECRET = python -c "import secrets; print(secrets.token_urlsafe(48))"
python -m uvicorn backend.api.main:app --reload
```

비밀번호는 입력 프롬프트로 지정한다(최소 8자). 공유 기본 비밀번호는 만들지 않는다.
`PPE_JWT_SECRET`은 32바이트 이상으로 설정한다. 운영 시 안전하게 보관하고 동일한 값을 재사용한다.
키 변경 시 기존 토큰은 무효화된다. 키 미설정 시 로그인은 503을 반환한다.
테스트 DB 경로는 `PPE_DB_PATH`로 분리할 수 있다.

- `POST /api/auth/login`: `{user_id,password}` → `{access_token,user_id,display_name,role}`
- `GET /api/auth/me`: Bearer 인증 사용자 정보
- `POST /api/auth/logout`: 해당 사용자의 모든 기존 세션을 무효화하고 미디어 쿠키 삭제
- JWT 유효기간 8시간. 비활성 사용자/만료·잘못된 토큰은 401, 권한 부족은 403.
- 기존 이벤트·통계·추천·방송 설정 API는 관리자 전용.
- 검토 body의 `reviewer_id`는 호환상 허용하지만 서버는 인증 사용자 ID만 저장.
- 탐지 대상 작업자와 로그인 계정을 연결하지 않는다.

## 프론트 연동

로그인 UI는 팀원의 `Feat/login-role-routing` 브랜치를 사용한다. 이 백엔드 브랜치는 로그인 화면을 재구현하지 않는다.
해당 브랜치 통합 시 `apiFetch`의 `credentials: 'include'`를 유지한다.
`VITE_AUTH_MOCK=false`로 실제 인증을 사용한다.
로그아웃은 Bearer로 `/api/auth/logout`을 호출한 후 localStorage 세션을 제거한다.

로그인 응답은 HttpOnly `ppe_media` 쿠키도 설정한다. 이 쿠키는 `/storage` 읽기 전용이며 API Bearer로 사용할 수 없다.
미디어는 관리자만 접근할 수 있고 Range 요청을 지원한다. API 요청에는 쿠키 대신 Bearer가 반드시 필요하다.
개발 시 프론트/백엔드 모두 `localhost` 또는 모두 `127.0.0.1`로 호스트명을 맞춘다.
서로 다른 PC에서는 Vite의 `/api`, `/storage` 프록시와 `VITE_API_BASE_URL=`을 권장한다.
운영 HTTPS에서는 `PPE_COOKIE_SECURE=true`를 설정한다. CORS 허용 origin은 `PPE_CORS_ORIGINS`(쉼표 구분)으로 지정한다.

## 검증

```powershell
python -m unittest backend.tests.test_auth -v
python -m backend.api.test_event_rereview_api
```

두 검증은 임시 DB를 사용한다. 로그인/권한/검토자 위조/만료/비활성화/로그아웃/미디어 Range 및 기존 재검토 흐름을 확인한다.

## 서버 → 현장 방송

이 브랜치는 인증 브랜치 위에 쌓인다. DB 초기화를 다시 실행하면 node/command 테이블이 추가된다.
서버의 `create_no_helmet_candidate_event(enable_tts=True)`는 이제 방송 명령을 생성한다.
서버에서는 실제 TTS를 실행하지 않는다. `enable_tts=False`는 방송 요청을 생략한다.
기존 `warning_broadcast_service.execute_warning_broadcast`는 로컬 진단용으로만 남겨두었다.

Swagger `/docs`에서 로그인 응답의 access_token을 Authorize에 넣은 뒤:

1. `POST /api/nodes`에 `{ "node_id": "field01", "name": "현장 방송 PC" }` 전송.
2. 응답의 node token은 현장 PC에 보관한다. DB에는 SHA-256 해시만 저장한다.
3. `PUT /api/cameras/CAM_001/node`에 `{ "source_node_id": "field01", "output_node_id": "field01" }` 전송.
4. 별도 터미널/현장 PC에서 다음 실행:

```powershell
$env:PPE_NODE_TOKEN = '<등록 응답의 node token>'
python -m field_agent.main --server http://localhost:8000
```

5. `GET /api/nodes`에서 online을 확인한다.
6. 방송 설정을 켜고 해당 PPE/구역/언어 템플릿을 저장한다.
7. 서버에서 이벤트를 생성하거나 기존 이벤트에 `POST /api/events/EVT_0001/broadcast`를 호출한다.
8. `GET /api/nodes/field01/commands`에서 실행 결과를 확인한다.

현장 PC는 HTTPX와 pyttsx3가 필요하다. 서버 주소에는 서버 PC의 LAN IP도 사용할 수 있다.
heartbeat 10초, offline 판정 30초, 명령 polling 2초, 방송 명령 유효기간 30초이다.
서버 프로세스와 현장 프로세스는 동일 PC에서도 독립 실행 가능하다.
영상 송출/서버 추론 연결은 후속 작업이다. 이번 단계는 기존 서버 이벤트 생성 함수와 방송 경로를 연결한다.

같은 이벤트/출력 PC의 방송 요청은 중복 생성하지 않는다. cooldown은 DB에 남아 서버 재시작 후에도 유지된다.
명령은 원자적으로 한 번 claim한다. 실행 후 응답 유실은 현장 `field_state/` journal로 결과 전송을 재시도한다.
claim 후 현장 프로그램이 종료된 명령은 자동 재생하지 않는다. 결과가 없으면 unknown으로 표시된다.
이 방식은 중복 방송 방지를 우선하며, 장애 시 exactly-once 재생을 보장하지 않는다.
오프라인일 때는 사건은 저장하고 방송은 `node_offline`으로 생략한다. 과거 방송을 늦게 재생하지 않는다.

```powershell
python -m backend.run_verification
```

기본 검증은 임시 DB를 사용하고 소리를 내지 않는다. 실제 음성 확인은 현장 프로그램에서 수행한다.

## PC별 voice 및 언어팩

관리자 Bearer로 아래 API를 사용한다. voice ID는 PC마다 다르므로 반드시 대상 PC의 조회 결과에서 선택한다.

| API | 용도 |
| --- | --- |
| `POST /api/nodes/{id}/voices/refresh` | 현장 PC에 재검색 명령 |
| `GET /api/nodes/{id}/voices` | 마지막 성공 조회의 음성 목록·조회 시각·선택 설정 |
| `PUT /api/nodes/{id}/voice` | `{ "language": "ko", "voice_id": "조회된 ID" }` 저장 |
| `POST /api/nodes/{id}/test-broadcast` | `{ "message": "방송 테스트입니다." }` 수동 시험 방송 |
| `POST /api/nodes/{id}/languages/install` | `{ "language": "ko-KR" }` 설치 요청 |

설치/조회 진행·실패 사유와 OS 설치 언어는 `/api/nodes/{id}/commands`의 result에서 확인한다.
음성 목록 조회 실패 시 이전 성공 목록을 유지하므로 `checked_at`과 최신 명령 상태를 함께 확인한다.
`language`는 방송 템플릿의 언어 값과 맞춘다(기존 템플릿: `ko`, `en`).

언어팩 자동 설치는 **대상 현장 PC**에서 관리자 권한 터미널로
`python -m field_agent.main --server <서버주소> --allow-language-install` 실행 시 허용한다.
일반 실행에서는 설치 요청이 `installation_not_enabled`, 관리자 권한이 없으면 `administrator_required`로 실패하며 수동 안내를 반환한다.
설치 명령은 고정된 `LanguagePackManagement/Install-Language`만 실행하고 UI 표시 언어를 변경하지 않는다.
초기 허용 언어는 ko-KR, en-US, en-GB, ja-JP, zh-CN, de-DE, fr-FR, es-ES이다.
설치에는 Windows 구성·다운로드·정책에 따른 제한이 있고 최대 20분 후 timeout 처리한다.
설치 후 실제 pyttsx3/SAPI5 voice를 재검색한다. **언어팩 설치 성공이 TTS voice 사용 가능을 보장하지 않는다.**
필요하면 Windows 설정에서 음성 기능 설치, 로그아웃/재부팅 후 재검색한다.
언어팩 설치 동안 heartbeat는 계속되지만 음성 실행 큐는 순차 처리하므로 설치는 모니터링 시작 전에 수행한다.

공식 명령 문서: https://learn.microsoft.com/en-us/powershell/module/languagepackmanagement/install-language

## 별도 프로세스 실증

```powershell
python scripts/verify_field_processes.py
python scripts/verify_field_processes.py --audio
```

임시 DB와 임시 Uvicorn 서버를 만든 뒤 별도 현장 프로세스로 HTTP 명령 수신·voice 보고를 확인한다.
`--audio`는 한국어 시험 문장 한 번을 실제 출력하고 서버의 성공 보고까지 확인한다.
2026-10-03 이 PC에서 한국어 Heami/영어 Zira 조회 및 한국어 출력 완료를 확인했다.
언어팩 실제 설치는 자동 검증에 포함하지 않는다. 권한 부족·비허용 언어·설치 timeout과 설치 성공 후 voice 부재는 mock으로 검증한다.
