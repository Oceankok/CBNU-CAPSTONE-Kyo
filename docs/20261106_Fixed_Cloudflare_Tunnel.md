# 고정 도메인과 서버 연결

관련 이슈: #129, #152. 기존 [HTTPS 터널 배포](20261028_Release_Tunnel_Target.md)를 그대로 사용합니다.

## 역할과 연결 경로

```text
카메라 PC / 브라우저
  → https://kyo.<구매한 도메인>
  → Cloudflare 관리형 터널
  → kyo-tunnel의 cloudflared
  → 기존 서버 Docker 네트워크의 http://web:8080
  → API / PostgreSQL
```

도메인의 네임서버 활성화와 서버 연결은 별도 단계입니다. 서버 IP를 DNS A 레코드에 넣거나 공유기에서 포트를 개방하지 않습니다. Cloudflare의 공개 호스트 이름 한 개만 웹 서비스로 연결합니다. 컨테이너 내부의 `localhost`는 서버 PC가 아니므로 터널의 대상은 **HTTP / `web:8080`**으로 지정합니다.

터널은 별도 Compose 프로젝트 `kyo-tunnel`입니다. 기존 `kyo-server`의 DB·볼륨·이미지·worker를 변경하지 않습니다. 기본 서버 Compose에는 터널을 포함하지 않으므로 일반 개발이나 CI에서 외부 연결이 생기지 않습니다. 현재 서버가 사용하는 Docker 엔진과 계정에서 실행해야 합니다.

## 한 번만 준비할 항목

1. 구매 도메인이 Cloudflare에서 활성화되어 있어야 합니다.
2. Cloudflare 계정의 **Networking → Tunnels**에서 관리형 터널을 생성합니다. 이전 메뉴에서는 **Zero Trust → Networks → Connectors**에 표시될 수 있습니다.
3. 설치 환경은 Docker를 선택합니다. 표시되는 명령을 별도로 실행하지 않고 터널 토큰만 로컬 파일에 저장합니다. 이 구성과 Windows 서비스 설치를 동시에 사용하지 않습니다.
4. 토큰 파일은 `<배포 폴더>/cloudflare/tunnel-token`입니다. 파일에는 토큰 문자열만 UTF-8로 저장합니다. 소스 저장소·채팅·이슈·명령 인수·`server.env`에는 토큰을 넣지 않습니다. 해당 파일과 상위 폴더의 읽기 권한은 운영 계정 및 필요한 관리자만 갖도록 설정합니다. Compose secrets는 파일 전달 방식이며 호스트 파일 자체를 암호화하지 않습니다.
5. cloudflared 공식 이미지를 내려받고 digest를 확정합니다. 아래 다운로드는 최초 연결 시 실행하며, 설정 검증에는 필요하지 않습니다.

```powershell
$ErrorActionPreference = 'Stop'
$deploymentRoot = Read-Host '기존 실제 서버 배포 폴더의 절대 경로'
docker pull cloudflare/cloudflared:latest
if ($LASTEXITCODE -ne 0) { throw 'cloudflared image download failed' }
$image = docker image inspect cloudflare/cloudflared:latest --format '{{index .RepoDigests 0}}'
if ($LASTEXITCODE -ne 0) { throw 'Image digest lookup failed' }
# $image에는 토큰이 아닌 cloudflare/cloudflared@sha256:... 주소만 들어갑니다.
./deploy/Cloudflare-Tunnel.ps1 -Action Validate -DeploymentRoot $deploymentRoot -Image $image
```

명령은 저장소 루트의 PowerShell 7에서 실행합니다. `Validate`는 설정 문법만 검사하며 컨테이너나 터널을 실행하지 않습니다. `server.env`·DB·개인 Docker 로그인 정보를 읽지 않습니다. 잘못된 이미지 주소, 비어 있는 토큰 파일, 다른 배포 프로젝트, LAN/Caddy 모드를 거절합니다.

## 최초 연결과 HTTPS 전환

다음은 실제 외부 공개 작업입니다. 토큰 저장과 공개 호스트 등록은 로컬에서 진행하며, 아래 준비가 끝나기 전까지 연결 완료로 판단하지 않습니다.

1. 우선 공개 호스트 경로 없이 커넥터만 시작합니다.

```powershell
./deploy/Cloudflare-Tunnel.ps1 -Action Start -DeploymentRoot $deploymentRoot -Image $image
```

2. Cloudflare에서 커넥터가 `Healthy`인지 확인합니다. 이 명령의 성공은 컨테이너 시작을 뜻하며 도메인·인증서·앱 접속 성공을 보장하지 않습니다.
3. `server.env`에서 `PPE_PUBLIC_HOST=kyo.<구매한 도메인>`, `PPE_COOKIE_SECURE=true`를 설정합니다. DB URL과 비밀키는 유지합니다. `/api/`와 `/storage/`는 캐시 우회 대상으로 유지하며 `Cache Everything` 같은 규칙을 적용하지 않습니다. 기존 API도 비공개 미디어에 `private, no-store`를 반환합니다.
4. 공개 호스트 `kyo.<구매한 도메인>`을 **HTTP / `web:8080`**에 연결하고, 같은 전환 작업에서 기존 `Release.ps1 -TunnelHttps`를 실행합니다. 기존 배포의 전체 커밋 SHA·API/Web digest·프로젝트를 사용합니다. `-Https`, `-Lan`, DB 초기화 옵션은 사용하지 않습니다. 호스트를 등록한 시점부터 외부 요청이 들어올 수 있으므로 전환 중 검증을 마치기 전에는 현장 PC를 연결하지 않습니다.
5. Release는 API의 Secure 쿠키 모드를 적용하고 공개 `https://<호스트>/health/ready`를 확인합니다. 실패하면 기존 방식대로 이미지를 복구합니다. **별도 터널은 Release가 종료하지 않으므로**, 전환에 실패하면 먼저 아래 `Stop`으로 외부 연결을 끄고 `server.env`를 기존 모드에 맞춰 복구합니다. 성공 여부는 `current-release.json`의 `tunnel_https=true`로 확인합니다.

로그인과 미디어 확인은 이후 HTTPS 주소에서 수행합니다. HTTP localhost에서 로그인에 성공하더라도 Secure 미디어 쿠키가 전달되지 않을 수 있으므로 이를 HTTPS 검증 결과로 대신하지 않습니다. CORS 허용 주소는 동일 출처 웹 배포에 추가하지 않습니다. Cloudflare가 HTTP 요청을 HTTPS로 보내도록 설정하고 인증서 오류를 우회하지 않습니다.

## 상태·중단·재시작

```powershell
./deploy/Cloudflare-Tunnel.ps1 -Action Status -DeploymentRoot $deploymentRoot -Image $image
./deploy/Cloudflare-Tunnel.ps1 -Action Stop -DeploymentRoot $deploymentRoot -Image $image
# 다시 켜려면 동일한 Start 명령을 사용합니다.
```

`Stop`은 터널만 중단합니다. 서버 DB·미디어는 유지됩니다. `restart: unless-stopped`이므로 정상 실행한 커넥터는 Docker 재시작 시 다시 시작됩니다. 단, 직접 `Stop`한 뒤에는 다음 부팅에도 중단 상태가 유지되므로 `Start`가 필요합니다. 서버의 기존 자동 시작은 Docker·웹·API·DB·배포 runner를 담당하고 GPU worker·추론은 계속 수동입니다. 이 PR은 Windows 예약 작업이나 기존 바로가기를 변경하지 않습니다.

Cloudflare 호스트 경로를 다른 주소로 바꾸지 않는 한 서버 공인 IP가 바뀌어도 같은 주소를 사용합니다. 인터넷이나 서버가 꺼져 있는 동안에는 접속할 수 없습니다. 터널 토큰을 바꾸면 로컬 토큰 파일도 갱신하고 `Start` 명령으로 컨테이너를 다시 생성합니다. 기존 커넥터가 동작 중인 상태에서 파일만 바꾸고 회전이 끝났다고 판단하지 않습니다.

## 현장 PC 주소와 시연 검증

최종 서버 주소는 `https://kyo.<구매한 도메인>`입니다. 영상 송신기와 field agent의 서버 주소를 같은 값으로 변경하며 Quick Tunnel 주소는 더 이상 사용하지 않습니다. 송신기 등록 스크립트는 기존 node 토큰으로 서버의 node 식별을 확인하므로, 이전 임시 서버의 토큰을 실제 서버에서 재사용할 수는 없습니다. 실제 서버에 이미 등록한 node만 주소 갱신으로 처리하고 나머지는 실제 서버에 등록합니다.

연결 후 다음 항목을 확인합니다.

- 공개 HTTPS readiness, 관리자 로그인·로그아웃, 인증된 미디어 조회 및 미인증·worker 접근 거부
- 다른 네트워크의 카메라 PC에서 MP4 업로드와 서버 수신, 명령 polling
- PC/Docker 재시작 후 같은 주소로 재접속 및 터널만 중단했을 때 외부 접속 차단
- GPU worker가 자동 실행되지 않는지 확인

이 구성은 영상 송신·이벤트 API를 변경하지 않습니다. 최종 모델과 작업 영역 판단을 연결하는 추론 통합은 별도 작업입니다. 실제 카메라·방송·GPU·외부 AI 검증을 설정 검사에 포함하지 않습니다.

## 근거와 격리 검사

- [Cloudflare 관리형 터널 생성](https://developers.cloudflare.com/tunnel/get-started/)
- [cloudflared `--token-file`](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/)
- [Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/)
- [Docker 재시작 정책](https://docs.docker.com/engine/containers/start-containers-automatically/)

`deploy/tests/Cloudflare-Tunnel.Tests.ps1`은 임시 폴더와 가짜 Docker로 실행 범위·프로젝트/네트워크 불일치·토큰 누락·실패 시 환경 복원을 확인합니다. 실제 Docker Compose 파서로 서비스와 포트·비밀 파일 전달 구조도 확인합니다. 실제 Docker 엔진·PostgreSQL·Cloudflare 계정·토큰은 필요하지 않습니다.
