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

### 촬영 세션 전송의 일시적 네트워크 장애 재시도

`video_sender.context_sender`의 업로드와 세션 생성·스트림 생성·세션 종료 요청은 기본적으로 일시적인 연결 오류/시간 초과와 HTTP 408·429·502·503·504를 재시도합니다. 선택 사항인 `VIDEO_BACKPRESSURE_ENABLED` 디스크 보호 설정과 별개입니다. 인증·인증서 오류와 다른 HTTP 오류를 자동 재시도하지 않습니다.

요청당 최대 8회, 재시도 예약 예산 60초, 개별 요청 timeout 최대 20초입니다. 대기는 1·2·4·8초 후 최대 8초이며 유효한 `Retry-After`가 더 길면 이를 따릅니다. 남은 예산보다 긴 힌트나 잘못된 힌트는 실패로 처리합니다. 대기 중 기존 guard를 최대 0.2초 간격으로 확인합니다. requests timeout은 소켓 무응답 제한이므로, 계속 조금씩 데이터가 도착하는 요청을 포함해 전체 작업이 정확히 60초 안에 중단된다는 보장은 없습니다.

재전송은 저장된 세션/스트림/조각 번호와 촬영 시각·길이·파일을 유지하며 매번 파일 처음부터 읽습니다. 올바른 접수 응답을 확인하고 ACK를 영속 저장한 뒤에만 파일을 지웁니다. 한도 초과나 오류에는 미확인 원본과 장부를 보존하고, 실행 중 캡처는 기존 정상 종료 요청을 따릅니다. 자동으로 새 카메라 촬영을 시작하지 않습니다. 캡처 종료 증거가 확보된 세션은 기존 `--resume-drain`으로 이어 보낼 수 있습니다.

이 보장은 `context_sender` 경로에만 해당합니다. `live_sender`의 오류 후 새 스트림 생성·45초 지난 조각 폐기 정책은 변경하지 않았습니다. 노트북의 사용자 수정 Dual 실행 파일이 어느 경로를 사용하는지는 해당 파일을 확인해야 하며, 서버 코드 업데이트만으로 노트북 전송기가 바뀌지 않습니다. 기존 카메라 선택·해상도·분할 길이·사용자 기능을 보존한 별도 병합과 시험 전에는 적용됐다고 판단하지 않습니다. 20초 장애 시험은 가짜 시계/네트워크 시험이며 실제 약한 인터넷 시험을 대신하지 않습니다.
