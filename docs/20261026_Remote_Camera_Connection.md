# 서로 다른 네트워크의 Windows 카메라·서버 연결 (#179)

## 구조와 현재 범위

`Windows 카메라 PC → OpenCV 캡처 → FFmpeg H.264·약 1초 MP4 → node Bearer 인증 HTTPS POST → Cloudflare Quick Tunnel → 서버 localhost 웹 프록시 → 영상 수신 API → 처리 node 전용 다운로드`

송신·수신 코드는 #164·#173·#158에 있고, 이번 변경은 수동으로 실행한 등록·암호화 설정·실행을 재현하도록 정리합니다. YOLO·Tracking·클립 생성 알고리즘과 프론트 화면은 변경하지 않습니다. 연속 영상 수신과 이벤트 클립 접수는 별도 경로이며, 서버 추론 연결은 #139에서 진행합니다.

원본 영상과 로그인·API 트래픽은 Cloudflare를 경유합니다. 이를 허용한 시연 자료로 검증합니다. 공개 주소에서도 관리자 로그인·node 토큰·처리 권한 검사를 유지합니다. PostgreSQL 포트와 서버 내부 API 포트는 외부에 직접 공개하지 않습니다.

## 서버 PC 준비

최신 main의 게시 API/web 이미지와 새 PostgreSQL 시연 DB를 사용합니다. 처음에는 [서버 배포 안내](20261013_Server_Release_Setup.md)에 따라 localhost로 초기화하고 관리자 계정을 생성합니다. 기존 개발 DB 이관은 필요하지 않습니다. 호스트 GPU worker는 [worker 안내](20261012_GPU_Worker_Setup.md)에 따라 별도 등록합니다.

서버에서 Docker를 사용할 수 있는 PowerShell 7을 사용합니다. 외부 접속 전에 선택한 시연 배포의 미디어 쿠키를 Secure로 전환합니다. 아래는 **이미 초기화된 localhost 시연 스택**만 대상으로 하며, DB 초기화·이미지 교체·자동 시작을 수행하지 않습니다. 잠시 API가 재시작됩니다.

```powershell
$ErrorActionPreference = 'Stop'
$deploymentRoot = Read-Host '시연 배포 폴더의 절대 경로'
$release = Get-Content (Join-Path $deploymentRoot 'current-release.json') -Raw | ConvertFrom-Json
if ($release.https -or $release.lan) { throw '이 예시는 localhost 시연 스택용입니다' }
$envFile = Join-Path $deploymentRoot 'server.env'
$config = [IO.File]::ReadAllText($envFile)
if ($config -notmatch '(?m)^PPE_COOKIE_SECURE=(true|false)\r?$') { throw '명시적인 쿠키 설정이 필요합니다' }
$config = [regex]::Replace($config, '(?m)^PPE_COOKIE_SECURE=false\r?$', 'PPE_COOKIE_SECURE=true')
[IO.File]::WriteAllText($envFile, $config, (New-Object Text.UTF8Encoding($false)))
$config = $null
$env:PPE_API_IMAGE = $release.api_image
$env:PPE_WEB_IMAGE = $release.web_image
$compose = Join-Path $deploymentRoot ("releases/" + $release.commit + '/compose.release.yaml')
docker compose --project-name $release.project --env-file $envFile -f $compose up -d --no-build --wait api maintenance
if ($LASTEXITCODE -ne 0) { throw '시연 HTTPS 쿠키 설정 적용 실패' }
```

이 구성은 Cloudflare가 HTTPS를 종료하고 로컬 origin에는 HTTP로 전달하는 방식입니다. `Release.ps1 -Https`의 Caddy·도메인·80/443 구성과는 다릅니다. 현재 Release 스크립트는 일반 HTTP 배포에서 Secure 쿠키 설정을 거절하므로, 다음 일반 HTTP 배포 전에는 터널을 중지하고 쿠키 설정을 false로 되돌려야 합니다. Secure 쿠키를 사용하는 동안 미디어 검토는 localhost HTTP 대신 공개 HTTPS 주소에서 수행합니다. 외부 주소가 바뀌어도 브라우저는 그 주소의 웹 프록시로 같은 origin의 API를 호출하므로 별도 CORS 허용 주소를 추가하지 않습니다.

[Cloudflare 공식 다운로드 안내](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/)에서 Windows AMD64 `cloudflared`를 준비합니다. 공식 GitHub 릴리스의 digest와 파일 SHA-256을 대조하고 버전을 기록합니다. 이번 검증에서는 2026.10.3을 사용했습니다. 실행 파일 경로와 웹 포트를 입력합니다. 카메라 PC에는 cloudflared가 필요하지 않습니다.

```powershell
$cloudflared = Read-Host 'cloudflared.exe 절대 경로'
$webPort = Read-Host 'localhost 웹 포트 (이번 시연은 8081)'
& $cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$webPort" --protocol http2 --metrics 127.0.0.1:0
```

출력된 `https://...trycloudflare.com` 주소를 카메라 PC에 전달합니다. 이 명령은 전경에서 실행하며 Ctrl+C로 종료합니다. 서비스·자동 시작을 등록하지 않습니다. 이메일 PIN 방식의 보호 옵션은 비대화형 송신 프로그램과 호환되지 않으므로 사용하지 않고 프로젝트의 node 인증을 사용합니다. 실제 계정으로 로그인하기 전 공개 `/health/ready`의 200, `/api/cameras`·`/api/processing/video/streams`의 미인증 401과 인증서 검증을 확인합니다.

## 카메라 PC 준비

Windows에서 Python·Git을 준비하고 송출용 폴더를 만듭니다. 전체 학습 데이터·서버 DB·YOLO 의존성은 받지 않습니다.

```powershell
$cameraRoot = Join-Path $env:USERPROFILE 'KyoCamera'
git clone --filter=blob:none --sparse https://github.com/Oceankok/CBNU-CAPSTONE-Kyo.git $cameraRoot
Set-Location $cameraRoot
git sparse-checkout set video_sender field_agent
python -m pip install --user --only-binary=:all: -r video_sender/camera-requirements.txt
```

이미 받은 폴더는 다시 clone하지 않고 해당 폴더에서 브랜치·변경 상태를 확인한 뒤 병합된 최신 main을 반영합니다. FFmpeg·ffprobe는 [Gyan Windows 배포처](https://www.gyan.dev/ffmpeg/builds/)의 release essentials ZIP을 사용합니다. ZIP과 같은 배포처의 `.sha256`을 받아 해시를 대조하고 `$env:USERPROFILE\KyoTools\ffmpeg` 아래에 압축을 풉니다. 영구 PATH를 수정할 필요는 없습니다. 다른 위치는 실행 스크립트의 `-FFmpegBin`을 사용합니다.

도구 종료 코드는 출력을 끝까지 받은 뒤 확인합니다. `ffprobe -version | Select-Object -First 1` 다음에 `$LASTEXITCODE`를 검사하면 파이프 조기 종료 때문에 잘못 실패로 판정할 수 있습니다.

```powershell
$output = & ffprobe -version 2>&1
$exitCode = $LASTEXITCODE
$output | Select-Object -First 1
if ($exitCode -ne 0) { throw 'ffprobe 실행 실패' }
```

[카메라 실행 안내](../video_sender/README.md)의 `Register-Camera.ps1`과 `Start-Camera.ps1`을 사용합니다. 기본 등록 ID는 `remote_cam_pc_01`, 카메라는 `REMOTE_CAM_01`입니다. 기존 검증 PC의 `KyoCameraConfig` 형식과 호환되므로 유효한 토큰을 재사용할 수 있습니다. Windows 암호화 파일은 다른 PC·계정으로 복사하여 사용할 수 없습니다. 비밀번호·node 토큰을 환경 설정 JSON이나 명령 인수에 쓰지 않습니다.

## 2026-10-26 실제 검증

- 서로 다른 네트워크에서 임시 HTTPS 웹 로그인에 성공했습니다.
- Windows 카메라 PC의 Python 3.13.3·requests 2.32.4·OpenCV 4.14.0·FFmpeg/ffprobe 9.0.2로 실제 송신했습니다.
- HTTP 202 반복, 서버에서 최근 MP4 3개 다운로드·각각 640×480·15fps·1초·15프레임 디코딩을 확인했습니다.
- 조회 시점 sequence gap은 0이며, 추가 5초 동안 새 영상 5개를 받았습니다. 서버 확인용 다운로드 파일은 임시 폴더에서 정리했습니다.
- 별도 인물 없는 합성 자료로 YuNet·SCRFD 각각 이미지·영상 4건의 실제 `onnxruntime-cuda` 처리·결과 조회·테스트 미디어 삭제를 확인했습니다. 얼굴 탐지 정확도 검증과 구분합니다.

위 검증은 채팅에서 수동 실행한 기존 프로그램과 설정 기준입니다. 이후 추가한 실행 스크립트의 격리 검증 결과는 PR에 별도로 기록합니다. 실제 카메라로 YOLO → 이벤트 → 클립 → 방송 → 검토가 자동 완료됐다는 의미는 아닙니다. 임시 원본 버퍼는 기존 보관 상한·maintenance를 따릅니다. 송신 프로그램을 종료해도 공개 터널은 별도로 종료해야 합니다.

## 주소를 고정하는 방법

변하는 것은 서버 IP를 확인한 결과가 아니라 Quick Tunnel의 임시 HTTPS **호스트명**입니다. 터널을 새로 만들면 주소가 바뀌며, 같은 프로세스를 유지하는 동안만 해당 주소를 사용합니다. [Quick Tunnel 공식 제한](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)에 따라 테스트용이며 가동 보장은 없습니다.

고정 주소는 [정식 Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/get-started/)에 Cloudflare 계정·연결된 도메인과 `api.example.com` 같은 서브도메인을 등록하는 방식으로 구성할 수 있습니다. 공유기 포트포워딩은 계속 필요하지 않습니다. 도메인을 이미 가지고 있다면 구매부터 할 필요는 없습니다. 도메인 비용·Cloudflare 서비스 요금 및 영상 전송에 적용되는 조건은 정식 서비스 선택 시 확인합니다. 이번 작업은 도메인 구매·계정 연결·정식 터널 전환을 실행하지 않습니다.

고정 주소 전환 후 카메라 PC에서 등록 스크립트에 새 주소를 전달하고 송신을 다시 시작합니다. 동일한 DB·node이면 기존 토큰을 검증해 재사용합니다. 정식 터널도 서버 PC·Docker·연결 프로그램이 실행 중이어야 하며 주소 고정이 서버 자동 시작을 의미하지 않습니다.
