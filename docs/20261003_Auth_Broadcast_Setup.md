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
