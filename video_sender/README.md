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

촬영 중인 영상은 `chunk_*.mp4.part`로 저장하고, FFmpeg 완료 목록에 기록된 뒤에만 `.mp4`로 전환합니다. 송신기는 `.part` 파일을 읽지 않습니다. 강제 종료 후 남은 `.part`는 재시작 시 덮어쓰거나 자동 전송하지 않습니다. 이 변경을 받을 때는 기존 촬영·송신을 모두 종료한 뒤 두 프로그램을 다시 실행합니다.

```powershell
Set-Location (Join-Path $env:USERPROFILE 'KyoCamera')
powershell -NoProfile -ExecutionPolicy Bypass -File video_sender/Start-Camera.ps1 -Mode Send
```

`[SENT] ... (202)`는 서버 접수 성공이며 추론 완료를 의미하지 않습니다. 캡처 창을 먼저 Ctrl+C로 종료하고 몇 초 뒤 송신 창도 종료합니다. 새 캡처는 완료된 파일에 `.ready` 표시를 남기므로 마지막 MP4도 전송합니다. 표시가 없는 구형 캡처 폴더는 마지막 파일 제외 규칙을 유지합니다. 전송·폐기 뒤 삭제 실패는 `.cleanup` 표시를 남겨 재전송 없이 재시도하며, 송신기 재시작 시에도 복구합니다. 삭제 대기 번호는 새 캡처에서 재사용하지 않습니다. HTTP 413/422로 영구 거절된 조각은 오류를 기록하고 폐기하여 후속 조각을 계속 전송합니다. 촬영을 다시 시작하면 기존 MP4·TXT의 가장 큰 번호 다음부터 저장하므로 출력 폴더를 비울 필요는 없습니다. 송신은 숫자 순서로 처리합니다. 촬영 재시작 시 기존 파일을 덮어쓰거나 일괄 삭제하지 않으며, 전송 성공·오래된 조각 폐기 후의 개별 파일 정리는 유지합니다. 같은 출력 폴더에서는 촬영 프로그램을 하나만 실행합니다.

`[BACKPRESSURE] ... video_buffer_full`은 서버의 임시 영상 버퍼 제한입니다. HTTP 429에서는 `Retry-After`만큼 기다린 뒤 같은 스트림·같은 조각을 재시도하며, 재연결하거나 전송 성공으로 처리하지 않습니다. 대기 중 촬영 후 45초를 넘긴 조각은 기존 정책대로 폐기합니다. 이 처리는 일시적인 제한에서 추적 상태가 불필요하게 초기화되는 것을 줄이며, 지속적인 전송 지연이나 영상 누락 자체를 없애지는 않습니다.

주소 변경 시 캡처·송신을 중지하고 `Register-Camera.ps1 -ServerUrl`을 새 주소로 다시 실행한 뒤 송신을 재시작합니다. 기존 토큰으로 새 주소의 heartbeat와 node ID를 확인한 뒤 설정을 저장합니다. 새 DB로 교체하여 토큰이 무효해진 경우에는 실패하며, 기존 서버의 토큰을 자동 폐기하거나 node를 재생성하지 않습니다. 새 DB에는 별도 `-ConfigRoot`와 필요 시 새 node ID로 등록합니다.

캡처와 송신에 다른 `-OutputDirectory`를 주면 서로 다른 폴더를 사용하므로 반드시 같은 값을 사용합니다. `video_sender/main.py`는 기존 2채널 시연 프로그램으로, 이 단일 카메라 실행과 동시에 시작하지 않습니다.
