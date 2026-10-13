# 서버 이미지·승인 배포·HTTPS 준비

관련 이슈: #129

API·PostgreSQL·웹은 Docker에서 실행하고 GPU worker와 현장 Windows TTS는 기존 네이티브 프로그램을 유지합니다. 기존 웹 화면과 nginx 설정은 변경하지 않습니다. 도메인·네트워크가 아직 미정이므로 외부 공개나 인증서 발급은 실행하지 않습니다.

## 빌드 및 승인 경계

`Release` workflow는 main의 push CI가 성공하면 GitHub 호스팅 runner에서 API·웹 이미지를 빌드하여 GHCR에 저장합니다. 이미지는 커밋 SHA 태그를 사용하고 실제 배포는 빌드 결과의 `sha256` digest를 지정합니다. 이미지 주소는 Actions 요약과 `release-references` artifact에서 확인합니다.

PR 및 PR CI 완료 이벤트는 서버 배포 runner를 실행하지 않습니다. 실제 배포는 main에서 `workflow_dispatch`의 `deploy=true`를 선택했을 때만 가능하며, `production` environment의 승인 이후 서버 runner가 실행됩니다. workflow는 production의 승인자 및 main 전용 브랜치 정책이 없으면 배포 전에 거부합니다.

GitHub에서 먼저 설정합니다.

1. Settings → Environments → `production`을 생성합니다.
2. Required reviewers를 지정하고 Deployment branches and tags를 **선택한 브랜치 main 하나만** 허용하도록 설정합니다.
3. environment variable `KYO_DEPLOY_ROOT`에 별도 서버 배포 폴더의 절대 경로를 지정합니다.
4. 서버 PC의 별도 배포 계정에 Windows X64 runner를 등록하고 `kyo-deploy` label을 추가합니다. PowerShell 7과 Linux Docker 엔진 접근이 필요합니다.
5. 공개 저장소 runner가 다른 workflow의 임의 작업을 받을 수 있으므로 runner 등록 전에 모든 self-hosted workflow와 저장소 접근 권한을 확인합니다. GitHub의 [self-hosted runner 보안 지침](https://docs.github.com/en/actions/reference/security/secure-use)을 따릅니다. 개인 ChatGPT/OAuth 자격 증명을 배포 계정과 공유하지 않습니다.

현재 구현은 runner 등록·environment 설정·실제 이미지 게시·운영 배포를 자동 실행하지 않습니다. 해당 준비는 별도 확인 후 진행합니다. environment 승인은 [GitHub 공식 배포 승인 문서](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/review-deployments)를 참고합니다.

## 첫 배포 — 수동 초기화

기존 `compose.yaml` 개발 스택과 별개인 `compose.release.yaml`을 사용합니다. 기본 프로젝트는 `kyo-server`이며 별도 영속 volume을 생성합니다. 개발 DB나 기존 volume을 자동으로 이관하지 않습니다. 처음에는 `PPE_PROCESSING_MODE=disabled`로 실행하고 worker 연결 후 `remote`를 사용합니다.

서버 PC에서 저장소 밖에 배포 폴더를 준비합니다. 예시는 프로젝트 루트 PowerShell에서 실행합니다.

```powershell
$deploymentRoot = Join-Path $env:USERPROFILE 'KyoServer'
New-Item -ItemType Directory -Path $deploymentRoot -Force
Copy-Item deploy/release.env.example (Join-Path $deploymentRoot 'server.env')
notepad (Join-Path $deploymentRoot 'server.env')
```

비밀키·DB 비밀번호·DB URL을 설정합니다. env 파일은 `KEY=value` 형태의 따옴표 없는 한 줄 값으로 저장하고, 비밀번호의 URL 예약 문자는 인코딩합니다. 비밀 파일·백업은 Git이나 Actions artifact에 올리지 않습니다.

main 병합 후 Actions의 Release가 게시한 실제 커밋 SHA와 digest 주소를 복사합니다. 첫 생성은 다음처럼 명시적으로 초기화합니다.

```powershell
$releaseCommit = Read-Host '게시한 40자리 커밋 SHA'
$apiImage = Read-Host 'Actions에 표시된 API ghcr.io/...@sha256:... 주소'
$webImage = Read-Host 'Actions에 표시된 Web ghcr.io/...@sha256:... 주소'
./deploy/Release.ps1 -DeploymentRoot $deploymentRoot -Commit $releaseCommit -ApiImage $apiImage -WebImage $webImage -InitializeDatabase
```

GHCR package가 비공개라면 실행 계정이 pull 권한을 갖고 있어야 합니다. CI 게시의 `GITHUB_TOKEN`과 로컬 계정은 별개입니다. 공개 package는 별도 로그인 없이 pull할 수 있습니다. API 및 웹은 기본적으로 localhost에만 노출되며 DB 포트는 공개하지 않습니다.

기본 seed는 비활성화합니다. 첫 관리자 계정은 배포한 이미지 주소를 유지한 상태에서 다음처럼 생성하며 비밀번호는 대화형 입력으로 받습니다.

```powershell
$env:PPE_API_IMAGE = $apiImage
$env:PPE_WEB_IMAGE = $webImage
$releaseCompose = Join-Path $deploymentRoot "releases/$releaseCommit/compose.release.yaml"
docker compose --project-name kyo-server --env-file (Join-Path $deploymentRoot 'server.env') -f $releaseCompose exec api python -m backend.auth.manage_users admin01 --role admin --name 관리자
```

별도 시연 DB는 시연 seed 문서를 사용하며 운영 스택에 더미 자료를 자동으로 삽입하지 않습니다.

## 후속 배포 및 복구

첫 배포 이후에는 `InitializeDatabase` 없이 같은 명령을 실행하거나 Actions에서 Release를 수동 실행합니다. 배포 전 PostgreSQL `pg_dump`를 서버의 `backups` 폴더에 복사하고, 게시된 이미지를 pull한 뒤 컨테이너 상태와 nginx를 경유한 API readiness를 확인합니다. 정상 완료한 커밋·digest는 `current-release.json`, 이전 값은 `previous-release.json`에 기록합니다. release 폴더의 Compose 설정은 해당 release와 함께 유지합니다.

애플리케이션 배포 실패 시 이전 이미지와 Compose 설정으로 복구하고 readiness를 확인합니다. DB는 자동으로 되돌리지 않습니다. 서버 설정 파일이나 프로젝트 이름을 배포 도중 변경하지 않습니다. 첫 배포 실패에는 이전 release가 없으므로 원인을 해결한 뒤 같은 release를 다시 실행합니다. volume을 지우는 `down -v`는 사용하지 않습니다.

동시 배포는 `release.lock`으로 차단합니다. 프로세스가 강제 종료되어 lock이 남으면 기록된 PID와 실행 상태를 확인한 뒤 해당 파일만 제거합니다. 같은 커밋에 다른 이미지 digest를 덮어쓰지 않으므로 재빌드로 digest가 달라진 경우 새 커밋의 release로 진행합니다.

수동으로 이전 release에 되돌리려면 다음을 실행합니다.

```powershell
$previousRelease = Get-Content (Join-Path $deploymentRoot 'previous-release.json') -Raw | ConvertFrom-Json
./deploy/Release.ps1 -DeploymentRoot $deploymentRoot -Commit $previousRelease.commit -ApiImage $previousRelease.api_image -WebImage $previousRelease.web_image -Https:([bool]$previousRelease.https)
```

DB 스키마 변경은 자동 배포 명령에서 수행하지 않습니다. 별도로 백업·이전 이미지와의 호환성·마이그레이션·복구 절차를 검토한 뒤 진행합니다. 이전 코드와 호환되지 않는 DB는 이미지 rollback만으로 복구할 수 없습니다. pg_dump는 DB 백업이며 미디어 volume까지 같은 시점으로 복사하는 백업은 별도 절차가 필요합니다.

## HTTPS 활성화

실제 도메인 또는 DDNS가 서버 공인 IP를 가리키고 TCP 80·443이 서버까지 연결되어야 합니다. CGNAT·학교 방화벽·공유기 포트 연결 여부는 별도로 확인합니다. 조건이 맞으면 Caddy가 정식 인증서 발급·갱신을 담당합니다. [Caddy HTTPS 공식 문서](https://caddyserver.com/docs/quick-starts/https)를 참고합니다.

`server.env`에서 `PPE_PUBLIC_HOST`와 `PPE_COOKIE_SECURE=true`를 설정하고 **첫 배포 때** `-Https`를 지정합니다. Actions 수동 실행도 `https=true`를 지정합니다. 이미 실행한 HTTP 환경을 HTTPS로 바꾸는 작업은 별도로 검토하여 이전 상태 기록과 gateway 설정을 함께 전환해야 하므로 자동 배포 명령에서 모드 변경을 거부합니다.

Caddy → 기존 nginx → API 경로를 사용합니다. 원본 저장소·PostgreSQL은 공개하지 않습니다. HTTPS 배포는 실제 도메인 주소로 인증서 검증을 포함한 readiness 확인을 수행하며 서버 자체에서도 도메인으로 접근할 수 있어야 합니다. 공인 접속 전 실제 로그인·미디어 쿠키·모바일 연결을 확인합니다.

도메인이 없는 동안에는 기존 개발 Compose의 같은 Wi-Fi 접속 방식을 사용할 수 있습니다. release HTTP 스택을 외부에 그대로 공개하지 않습니다.

## 검증

검증 실행 전 승인을 받습니다. 배포 스크립트의 격리 테스트는 `deploy/tests/Release.Tests.ps1`로 실행하며 실제 Docker 대신 가짜 명령을 사용해 초기화 제약·백업·실패 복구·정상 갱신을 확인합니다. 실제 release Compose 실행은 별도 테스트용 DB·volume·포트로 검증합니다. GHCR 게시·실제 서버 배포·인증서 발급은 해당 검증에 포함하지 않습니다.

2026년 10월 13일 검증 결과:

- PowerShell 격리 테스트에서 초기화 제한·HTTPS 설정 제한·DB 백업·실패 시 이전 이미지 복구·성공 기록·환경변수 복원·동시 배포 차단을 확인했습니다.
- API·웹 이미지를 빌드하고 별도 PostgreSQL volume에 초기화했습니다. nginx를 경유한 readiness·로그인·관리자 카메라 조회·미인증 401·SPA 주소 접근을 확인했습니다.
- 컨테이너 재생성 후 PostgreSQL 계정이 유지됐습니다. API·웹은 localhost에만 바인딩하고 DB는 외부 포트를 제공하지 않는 것을 확인했습니다.
- Caddy 설정을 네트워크 없는 컨테이너에서 검증했습니다. 실제 DNS·포트 연결·인증서 발급 검증은 아닙니다.
- actionlint로 workflow 문법을 검증했습니다. 테스트 컨테이너와 volume은 정리했으며 실제 개발 DB·GPU·방송·외부 VLM은 사용하지 않았습니다.
- GHCR 실제 게시·GitHub 승인 environment 및 runner 연동·외부 HTTPS·운영 복구는 설정 후 별도 검증 대상입니다.
