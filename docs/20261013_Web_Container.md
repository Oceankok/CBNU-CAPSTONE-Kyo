# 웹 컨테이너 (nginx) — #129

브라우저는 이 서버 하나(같은 출처)로만 접속함. 빌드된 React 화면을 내주고, `/api`·`/storage`·`/health/ready`를 API 컨테이너로 연결함. 같은 출처라 CORS 설정이 필요 없고, 미디어 쿠키(`/storage` 전용)도 그대로 동작함.

## 구성
- `deploy/web.Dockerfile`: Node로 `npm run build` 후 nginx(비루트, `nginxinc/nginx-unprivileged`)에 복사함. `VITE_API_BASE_URL`은 비워서 같은 출처로 호출함.
- `deploy/nginx.conf`: 정적 파일, 프록시, SPA fallback.
- `compose.yaml`의 `web` 서비스: API가 healthy가 된 뒤 시작함. 읽기 전용 파일시스템, 모든 capability 제거.

## nginx 동작
- `/` : 파일이 없으면 `index.html` (새로고침해도 `/review/EVT_0001` 같은 주소가 열림). `index.html`은 `no-cache`.
- `/assets/` : 해시된 빌드 파일, 1년 캐시.
- `/api/` : API로 프록시. 이미지·영상 업로드는 버퍼링 없이 전달, 본문 최대 110MB(백엔드 한도 100MB), 읽기 제한 120초(장비 사진 분석이 외부 호출을 포함함).
- `/storage/` : 비식별 미디어 프록시. `Range` 요청을 그대로 전달해서 영상 탐색이 동작함.
- `/health/ready` : API 준비 상태.
- **`/docs`, `/openapi.json`은 프록시하지 않음** (화면으로 처리됨). 외부에 API 문서를 노출하지 않음.
- 보안 헤더(`nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`)를 모든 응답에 적용함.

## 실행
저장소 루트에서 (`.env`는 `docs/20261012_Server_Docker_Runtime.md` 참고):

```bash
docker compose build
docker compose --profile tools run --rm db-init      # 최초 1회
docker compose up -d api maintenance web
docker compose exec api python -m backend.auth.manage_users admin01 --role admin --name 관리자
```

접속: `http://127.0.0.1:8080` (포트는 `PPE_WEB_PORT`로 변경).

### 같은 Wi-Fi의 폰에서 접속
`.env`에 `PPE_WEB_BIND=0.0.0.0`을 설정하고 `docker compose up -d web`을 다시 실행함. 폰에서 `http://<서버 PC의 LAN IP>:8080`으로 접속함. 서버 PC 방화벽에서 해당 포트를 허용해야 함. HTTP 접속이므로 `PPE_COOKIE_SECURE=false`를 유지함.

## HTTPS (후속)
현재 nginx는 HTTP만 처리함. 정식 인증서가 준비되면 다음 중 하나를 선택함.
- nginx에 인증서를 마운트해서 443을 직접 처리함.
- 앞단에 Caddy 등을 두고 이 컨테이너로 프록시함.

어느 쪽이든 `.env`의 `PPE_COOKIE_SECURE=true`로 바꿔야 함. API는 `--no-proxy-headers`로 실행되므로 신뢰할 프록시가 정해지면 해당 프록시만 허용하도록 변경함.

## 현장 PC
현장 프로그램도 이 주소(`--server http://<서버>:8080`)로 접속할 수 있음. `/api/field/*`가 같은 프록시를 지남.

## 검증 (2026-10-13, macOS Docker Desktop, 별도 프로젝트·신규 volume)
- 이미지 빌드, 컨테이너 healthy, 비루트(nginx) 실행, 이미지 77MB.
- `/` 200, `/review/EVT_0001` 200(SPA), 없는 `/assets/*` 404, `/health/ready` 200, `/api/events` 무토큰 401.
- 로그인이 프록시를 통해 동작하고 `ppe_media` 쿠키가 `/storage` 경로로 설정됨. `/storage` 무쿠키 401.
- 비식별 완료 미디어(검증용 파일): 전체 200, `Range` 요청 206과 `Content-Range` 확인.
- 50MB 업로드가 nginx에서 413으로 거절되지 않고 API까지 도달함 (API-only 모드이므로 503 `processing_worker_not_connected`).
- Chrome으로 로그인, 검토 상세 새로고침(주소 유지), 이미지 표시, 설정 화면 이동 확인.
- 테스트 후 컨테이너·volume·이미지를 모두 제거함.
