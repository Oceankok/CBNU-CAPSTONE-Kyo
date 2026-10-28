# CD 배포 대상과 기존 HTTPS 터널 연결

관련 이슈: #129, #151, #152.

이 변경은 기존 시연 서버를 Actions에서 선택하고 이미 실행한 HTTPS 터널을 유지한 채 이미지를 갱신하기 위한 것입니다. SQLite 데이터 이관, DB 자동 복구, 터널 설치·자동 시작, 도메인 구매, 실제 배포는 추가하지 않습니다.

## 배포 폴더와 프로젝트는 한 쌍입니다

production environment에서 다음 변수를 함께 설정합니다.

| 변수 | 값 |
| --- | --- |
| `KYO_DEPLOY_ROOT` | 기존 `server.env`·`current-release.json`이 있는 배포 폴더의 절대 경로 |
| `KYO_DEPLOY_PROJECT` | 그 manifest의 `project`와 같은 Compose 프로젝트 이름 |

`KYO_DEPLOY_PROJECT`를 생략하면 기존 동작인 `kyo-server`입니다. 별도 시연 서버가 `kyo-demo`라면 해당 값을 명시합니다. 프로젝트 이름은 영속 volume의 선택에 영향을 주므로 폴더 이름에서 추측하거나 기존 프로젝트를 자동으로 바꾸지 않습니다. 이름 불일치는 Docker 실행 전에 거절합니다.

변수를 바꿀 권한이 없으면 저장소 관리자에게 두 값을 요청합니다. 이 코드는 GitHub 환경 변수나 기존 서버 설정을 자동으로 변경하지 않습니다.

## 접속 방식

| 선택 | 바인딩·진입점 | 쿠키 |
| --- | --- | --- |
| 옵션 없음 | localhost HTTP | `PPE_COOKIE_SECURE=false` |
| `-Lan` | 웹 포트만 신뢰하는 LAN에 HTTP 공개 | `false` |
| `-Https` | Caddy가 80/443에서 직접 HTTPS 제공 | `true` |
| `-TunnelHttps` | 기존 외부 HTTPS 터널 → localhost 웹 포트 | `true` |

세 옵션은 동시에 선택할 수 없습니다. 터널 모드는 Caddy를 시작하거나 80/443을 개방하지 않습니다. API와 PostgreSQL의 기존 접근 경계도 유지합니다. 두 HTTPS 방식 모두 `server.env`의 `PPE_PUBLIC_HOST`에 실제 호스트 이름을 지정해야 합니다. 프로토콜·경로·포트·인증 정보를 포함하지 않습니다.

예를 들어 Quick Tunnel이 출력한 주소에서 `https://`를 제외한 호스트 이름만 사용합니다. 고정 이름의 관리형 터널도 같은 모드를 사용합니다. Quick Tunnel은 실행마다 이름이 바뀌며 시연·개발 용도입니다. 고정 주소가 필요하면 도메인과 관리형 터널을 별도로 준비합니다. [Cloudflare 공식 안내](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)를 참고합니다.

## 기존 터널을 사용하는 수동 갱신

기존 데이터베이스가 초기화되어 있고 게시한 이미지와 현재 DB 스키마가 호환되는 서버를 전제로 합니다. 터널을 별도로 실행하고 그 진입점이 현재 웹 포트로 연결되는지 확인합니다. 예시는 직접 실행할 때만 터널을 엽니다.

```powershell
$webPort = Read-Host 'server.env에 설정한 웹 포트'
cloudflared tunnel --url "http://127.0.0.1:$webPort"
```

`server.env`에서 `PPE_PUBLIC_HOST`를 출력된 호스트 이름으로, `PPE_COOKIE_SECURE`를 `true`로 설정합니다. 비밀키·DB URL은 유지합니다. 다른 PowerShell에서 게시된 SHA와 이미지 digest를 사용합니다.

```powershell
$deploymentRoot = Read-Host '기존 시연 서버 배포 폴더의 절대 경로'
$project = Read-Host 'current-release.json의 project 값'
$releaseCommit = Read-Host '게시한 40자리 커밋 SHA'
$apiImage = Read-Host '게시한 API ghcr.io/...@sha256:... 주소'
$webImage = Read-Host '게시한 Web ghcr.io/...@sha256:... 주소'
./deploy/Release.ps1 -DeploymentRoot $deploymentRoot -Project $project -Commit $releaseCommit -ApiImage $apiImage -WebImage $webImage -TunnelHttps
```

서버 내부 readiness 이후 실제 `https://PPE_PUBLIC_HOST/health/ready`에 인증서를 검증하여 접근합니다. 터널이 없거나 주소가 잘못되어 실패하면 성공 manifest를 기록하지 않습니다. 기존 release가 있다면 기존 이미지·바인딩·실행 중 쿠키 모드로 되돌립니다. DB 자동 복원은 수행하지 않습니다.

터널 자체나 그 주소를 스크립트가 만들거나 변경하지 않습니다. gateway 프로필을 사용하는 Caddy HTTPS와 다른 모드이므로 `-Https`를 함께 전달하지 않습니다. 임시 주소가 바뀌면 이 설정과 현장 프로그램의 서버 주소를 갱신합니다.

## Actions에서 적용

main의 Release 수동 실행에서 `deploy=true`, `tunnel_https=true`, `https=false`, `lan=false`를 선택합니다. production 승인·main 전용 정책·배포 runner·게시 이미지 digest 조건은 그대로 적용합니다. 자동 이미지 게시가 실제 배포로 바뀌지는 않습니다.

배포 계정에 Docker·배포 폴더 접근 권한이 있어야 합니다. 네이티브 worker와 TTS는 이 배포가 자동으로 재시작하지 않습니다. API 계약 변경이 있다면 별도 합의된 절차로 worker 버전과 설정을 맞춥니다. Codex 로그인이나 API 키를 배포 job에 복사하지 않습니다.

## 기록과 모드 전환

manifest의 `https`는 기존 Caddy 모드를 뜻하고 새 `tunnel_https`가 터널 모드를 구분합니다. 이전 manifest에 이 필드가 없으면 `false`로 처리합니다. 이미지가 같아도 LAN·터널 모드가 바뀌면 이전 기록을 보존합니다.

localhost ↔ 터널 모드를 전환할 때 `server.env`의 쿠키 값을 선택에 맞게 바꿉니다. 터널이 계속 실행 중이라면 localhost 모드만 선택해도 기존 외부 주소가 닫히지는 않습니다. 외부 접속을 중단하려면 터널 프로세스를 별도로 종료합니다. Caddy와 다른 모드 간 전환은 기존 별도 검토 원칙을 유지합니다.

배포 스크립트는 Compose 실행 중 쿠키 변수를 선택한 모드로 고정하고 종료 후 호출자의 환경을 복원합니다. 실패 복구는 프로세스 내 기존 쿠키 모드를 복원하며 `server.env` 자체를 수정하지 않습니다. 실패 후 별도 수동 기동을 한다면 파일의 쿠키 값도 실제로 유지할 모드와 맞춰야 합니다.

이 기능은 이미 적용된 스키마에서 이미지를 교체하는 범위입니다. DB 스키마가 다른 이미지로 넘어가는 작업은 별도 초기화·호환성 확인이 필요하며, 닫은 #174의 자동 이관·복구 기능을 전제로 하지 않습니다.

## 검증 경계

격리 테스트는 가짜 Docker·가짜 HTTPS 응답으로 모드 배타성, 설정 거부, localhost 바인딩, 외부 HTTPS 확인, 실패 시 이미지·쿠키 모드 복구, legacy manifest, 프로젝트 이름 전달·불일치 거부, 환경 복원과 배포 잠금을 확인합니다. 실제 도메인·포트·터널·Docker 서비스·유료 API를 사용하지 않습니다. 실제 production 설정과 승인 배포는 별도 실행 대상입니다.
