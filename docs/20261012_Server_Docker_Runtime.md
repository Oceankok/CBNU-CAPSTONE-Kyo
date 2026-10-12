# 서버 API Docker 실행 환경 — #129 1단계

현재 단계에서는 SQLite 기반 API와 maintenance를 Docker Compose로 실행하도록 구성합니다. PostgreSQL·HTTPS·웹 컨테이너·CD는 후속 작업입니다. #127은 아직 미구현이며, PostgreSQL 스키마 전환은 해당 기능 반영 후 진행합니다.

## 구성 및 책임

- `api`: 인증·DB·조회·검토·방송 명령 관리 API입니다. 기본 노출은 서버 PC의 `127.0.0.1:8000`입니다.
- `db-init`: tools 프로필에서 명시적으로 실행하는 DB 초기화입니다.
- `maintenance`: API readiness 성공 이후 시작하며 보관 만료·삭제 재시도·임시 파일 정리를 수행합니다.
- `sqlite_data`, `media_data`, `private_media`: DB·공개 경로의 처리 결과·비공개 원본 및 근거 저장소입니다. 컨테이너 제거 후에도 유지됩니다.
- 웹 Dockerfile·프록시·SPA fallback은 프론트 담당이 추가합니다. 프론트 파일은 수정하지 않았습니다.

이미지에는 CUDA·ONNX Runtime·Windows TTS·ChatGPT 로그인 정보를 포함하지 않습니다. 별도 `backend/requirements-api.txt`에 headless OpenCV 등 API import에 필요한 의존성을 정의했습니다. 기존 Windows 개발 의존성은 유지합니다.

## GPU 처리 경계

현재 네이티브 API는 미디어 업로드·재처리와 장비 후보 분석 중 GPU를 직접 호출합니다. 호스트 GPU worker 연결은 아직 구현하지 않았습니다.

컨테이너에서는 `PPE_LOCAL_PROCESSING=false`로 아래 요청을 **본문 저장·GPU 호출·외부 이미지 전송 전에 503 `processing_worker_not_connected`로 거절**합니다.

- `POST /api/events/{event_id}/media`
- `POST /api/media/{media_id}/redact`
- `POST /api/equipment/recognitions`

인증·조회·검토·보관/삭제·장비 수동 관리·방송 명령 API는 사용 가능합니다. 기본 네이티브 실행은 처리 기능을 유지합니다. 컨테이너에서 설정만 true로 변경하여 GPU 기능을 활성화하지 않습니다.

후속 worker는 원본 접수 → 작업 등록 → 호스트 GPU 처리 → 결과 검증·게시 구조로 설계합니다. 기존 삭제·보관 트랜잭션과 처리 상태를 유지하고, 삭제된 자료가 늦은 처리 완료로 다시 게시되지 않아야 합니다. 이미지 파일 경로를 임의로 수신하거나 원본을 공개 URL로 반환하지 않습니다. SQLite named volume을 Windows 프로그램에 직접 공유하는 방식은 사용하지 않습니다. 작업 접수·결과 반영 API와 미디어 전달 방식은 PostgreSQL 전환 및 통합 담당과 함께 확정합니다.

Docker Desktop의 Windows GPU 지원은 WSL2 등의 조건이 있으므로 이번 단계에서는 GPU 프로세스를 컨테이너 밖에 유지합니다. [Docker GPU 안내](https://docs.docker.com/desktop/features/gpu/)

## 준비 및 실행 — 승인 후 수행

아래 명령은 이미지 다운로드·패키지 설치·DB 생성·서버 실행을 발생시킵니다. 2026-10-12 별도 테스트 프로젝트와 신규 volume에서 빌드·실행 검증을 완료했습니다. 아래 기본 배포 프로젝트는 실행하지 않았습니다. Docker Desktop Linux containers와 Compose v2 이상이 필요합니다.

```powershell
Set-Location C:\Users\robin\Projects\CBNU-CAPSTONE-Kyo
Copy-Item .env.example .env
$secret = python -c "import secrets; print(secrets.token_urlsafe(48))"
$settings = (Get-Content .env -Raw).Replace('PPE_JWT_SECRET=', "PPE_JWT_SECRET=$secret")
Set-Content .env -Value $settings -Encoding utf8
Remove-Variable secret, settings
docker compose config --quiet
docker compose build api
# 새 Docker volume에 초기화합니다. 기존 Windows 개발 DB는 마운트하지 않습니다.
docker compose --profile tools run --rm db-init
docker compose up -d api maintenance
docker compose exec api python -m backend.auth.manage_users admin01 --role admin --name 관리자
Invoke-RestMethod http://127.0.0.1:8000/health/ready
docker compose ps
```

`db-init`은 seed 카메라·설정을 넣는 초기화이므로 매 시작마다 실행하지 않습니다. 기존 배포 DB의 업그레이드는 백업 후 별도 마이그레이션 절차로 수행합니다. readiness는 DB 파일·스키마 버전·사용자 테이블을 확인하며 빈 DB를 생성하지 않습니다. `local_processing=false`는 정상적인 API-only 기능 상태이며 영상 처리 완료를 의미하지 않습니다.

같은 .env·프로젝트 이름 `kyo`를 유지하여 재실행합니다. `.env`를 다시 생성하면 기존 토큰이 무효화되므로 최초 준비 시에만 위 복사·생성 명령을 수행합니다. Windows 개발 DB·계정·미디어는 자동 복사하지 않습니다. `docker compose down`은 서버를 정지하며 **`down -v`는 영속 DB·미디어를 삭제하므로 사용하지 않습니다.**

## 검증 계획

1. 새 runtime 테스트와 백엔드 전체 회귀 테스트: 격리 DB·가짜 GPU/외부 응답, 실제 개발 DB·GPU·방송은 사용하지 않습니다.
2. Compose 설정 검사·이미지 빌드: 기본 이미지와 패키지를 내려받습니다.
3. 신규 Docker volume에 DB 초기화·관리자 생성 후 readiness·로그인·장비 조회를 확인합니다.
4. 인증 후 GPU 의존 요청이 503으로 차단되고 원본·후보가 저장되지 않는지 확인합니다.
5. 컨테이너 재생성 후 DB·미디어 유지와 maintenance 실행을 확인합니다.

단계별로 승인받고 실행합니다. `PPE_COOKIE_SECURE=false`는 현재 로컬 HTTP용입니다. 외부 공개 시 HTTPS·쿠키·CORS·프록시 신뢰·접근 주소를 함께 구성합니다. 현재 Uvicorn은 프록시 헤더를 신뢰하지 않습니다. 신뢰할 웹 프록시가 확정된 뒤 해당 프록시만 허용하도록 변경합니다.

## 후속 단계

### 2026-10-12 검증 결과

- runtime 테스트 3개가 통과했습니다. 네이티브 기본 처리 유지, API-only 요청의 부작용 전 차단, readiness의 누락 DB 비생성을 확인했습니다.
- 이를 포함한 백엔드 전체 회귀 테스트 65개가 통과했습니다. 임시 SQLite DB와 가짜 GPU·외부 응답을 사용했습니다.
- Docker 설치 후 Compose 설정·API 이미지 빌드·컨테이너 실행을 별도 프로젝트의 신규 volume에서 검증했습니다.
- readiness 및 API-only 상태, 관리자 로그인·장비 조회, 미인증 401, GPU 의존 요청 3종의 503 차단 및 미디어·후보 미생성, maintenance 실행을 확인했습니다.
- 컨테이너 재생성 후 관리자 계정과 처리 결과·비공개 저장소의 검증용 파일이 유지됐습니다. 실제 영상 처리 품질 검증은 아닙니다.
- 테스트 종료 후 해당 프로젝트의 컨테이너·volume을 제거했습니다. 빌드 이미지·캐시는 재사용을 위해 유지했습니다. 기존 개발 DB·미디어·GPU·방송은 사용하지 않았습니다.
- 최초 빌드에서는 실행 프로세스의 PATH에 Docker credential helper 경로가 없어 실패했습니다. 해당 프로세스에 Docker bin 경로를 추가한 뒤 재실행하여 통과했습니다. 시스템 PATH·Docker 로그인 설정은 변경하지 않았습니다.

### 남은 작업

- #127 반영 이후 PostgreSQL 전환 및 PostgreSQL CI service 구성
- 호스트 GPU worker 작업·결과 API와 추론 우선 큐 설계
- 프론트 담당의 웹 컨테이너 및 동일 출처 `/api`·`/storage` 프록시
- 외부 접속 네트워크·정식 HTTPS 인증서·내부 Wi-Fi 대안 검증
- GitHub 호스팅 runner에서 PR 검증·이미지 빌드, 보호된 main 또는 승인된 수동 배포에서만 서버 runner 실행

참고: [Compose 시작 순서](https://docs.docker.com/compose/how-tos/startup-order/), [필수 환경변수](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)
