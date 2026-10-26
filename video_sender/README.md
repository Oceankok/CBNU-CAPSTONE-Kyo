# Windows 카메라 PC 연결

카메라 PC는 캡처·H.264 MP4 분할·HTTP 송신을 수행합니다. YOLO 추론은 서버 PC의 별도 프로그램에서 담당합니다. 카메라 PC에 PyTorch·학습 데이터·얼굴 모델을 설치하지 않습니다.

외부 HTTPS 연결·도구 설치·검증 결과·고정 주소 전환은 [원격 카메라 실행 안내](../docs/20261026_Remote_Camera_Connection.md)를 따릅니다. sparse checkout으로 이 폴더만 받은 경우 문서는 GitHub에서 확인할 수 있습니다.

```powershell
Set-Location (Join-Path $env:USERPROFILE 'KyoCamera')
python -m pip install --user --only-binary=:all: -r video_sender/camera-requirements.txt
$serverUrl = Read-Host '서버 HTTPS 주소'
powershell -NoProfile -ExecutionPolicy Bypass -File video_sender/Register-Camera.ps1 -ServerUrl $serverUrl
```

관리자 비밀번호는 로컬 입력으로 받으며, node 토큰은 기본적으로 저장소 밖 `$env:USERPROFILE\KyoCameraConfig\node-token.xml`에 Windows DPAPI로 암호화하여 저장합니다. 같은 Windows 계정·PC에서 사용합니다. 암호화 파일도 Git·채팅에 첨부하지 않습니다. 출력 PC는 송출 PC와 같은 node로 연결하지만 이 스크립트가 방송 프로그램을 실행하지는 않습니다.

FFmpeg·ffprobe가 PATH에 없으면 기본 사용자 도구 폴더 `KyoTools/ffmpeg`의 한 설치본을 찾습니다. 다른 위치는 `-FFmpegBin`으로 지정합니다. 영구 PATH는 변경하지 않습니다.

첫 번째 PowerShell에서 캡처합니다. 카메라 앱 등 다른 프로그램은 먼저 닫습니다. 기본 카메라는 0번이며 `-CameraIndex 1`처럼 선택할 수 있습니다.

```powershell
Set-Location (Join-Path $env:USERPROFILE 'KyoCamera')
powershell -NoProfile -ExecutionPolicy Bypass -File video_sender/Start-Camera.ps1 -Mode Capture -CameraIndex 0
```

두 번째 PowerShell에서 송신합니다.

```powershell
Set-Location (Join-Path $env:USERPROFILE 'KyoCamera')
powershell -NoProfile -ExecutionPolicy Bypass -File video_sender/Start-Camera.ps1 -Mode Send
```

`[SENT] ... (202)`는 서버 접수 성공이며 추론 완료를 의미하지 않습니다. 캡처 창을 먼저 Ctrl+C로 종료하고 몇 초 뒤 송신 창도 종료합니다. 송신기는 마지막 파일을 작성 중인 것으로 보고 남기므로 종료 후 마지막 MP4·TXT가 남을 수 있습니다. 두 프로세스가 모두 종료한 뒤 선택한 출력 폴더의 잔여 파일을 직접 확인·정리합니다. 실행 스크립트는 기존 영상을 자동 삭제하지 않습니다.

주소 변경 시 캡처·송신을 중지하고 `Register-Camera.ps1 -ServerUrl`을 새 주소로 다시 실행한 뒤 송신을 재시작합니다. 기존 토큰으로 새 주소의 heartbeat와 node ID를 확인한 뒤 설정을 저장합니다. 새 DB로 교체하여 토큰이 무효해진 경우에는 실패하며, 기존 서버의 토큰을 자동 폐기하거나 node를 재생성하지 않습니다. 새 DB에는 별도 `-ConfigRoot`와 필요 시 새 node ID로 등록합니다.

캡처와 송신에 다른 `-OutputDirectory`를 주면 서로 다른 폴더를 사용하므로 반드시 같은 값을 사용합니다. `video_sender/main.py`는 기존 2채널 시연 프로그램으로, 이 단일 카메라 실행과 동시에 시작하지 않습니다.
