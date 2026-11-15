# 버전 관리되는 추론 실행 경로

기본 실행은 기존 legacy 다중 카메라 추론과 isolate 정책을 유지합니다. PR #258의 기본 복구는
배포된 저장소의 CLI 기본값으로 이미 연결되므로, 이를 켜기 위해 개인 런처에 새 플래그가 필요했던 것은 아닙니다.
이 변경은 선택 설정을 추적 가능한 코드로 전달하고 초기화·종료·프로세스 소유권을 보완합니다.
개인 `KyoServer/Launcher` 파일·JSON·암호화 토큰·예약 작업·운영 Python은 변경하지 않습니다.

## 설정과 실행

`deploy.host_runtime.settings.arguments`는 기존 camera_ids/model_path/mode를 유지하며
buffer_mib, failure_policy, camera/reference 재시도 한도·간격, preflight_timeout을 전달합니다.
생략한 옵션은 CLI 기본값을 사용합니다. 알 수 없는 키·중복 카메라·bool/NaN/무한대 수치·상대 모델 경로는 거절합니다.
모델·프로필은 존재하는 절대 경로여야 합니다. 인증 정보는 설정에 저장하지 않고 기존 환경에서 상속합니다.

```powershell
python -m deploy.host_runtime.entry --source C:\Source\Kyo --settings C:\Private\inference-settings.json --check-settings
```

이 명령은 설정/프로필을 검사하며 모델 로딩·HTTP·상태 생성·추론을 시작하지 않습니다.
실제 시작 시에는 check-settings를 제외합니다. 서버/호스트 버전 일치 및 remote 처리 모드를 확인하고
run_multi를 실행합니다. 출처가 다른 checkout에서 module을 실행하면 거절합니다.

```powershell
pwsh -NoProfile -File .\deploy\host_runtime\Start-Inference.ps1 -Python C:\PythonEnv\Scripts\python.exe -SourceRoot C:\Source\Kyo -SettingsPath C:\Private\inference-settings.json -RuntimeDirectory C:\Private\runtime
```

PowerShell 7.2 이상 wrapper는 인자 경계를 보존하고 숨겨진 자식을 실행합니다.
stdout/stderr 로그와 PID/start_ticks를 지정한 runtime 폴더에 남기며 실제 자식 ExitCode를 반환합니다.
운영 예약 작업을 자동 변경하지 않습니다. 기존 안전한 credential 로딩 단계 뒤에 이 명령을 연결하는 것은
사용자가 검토한 배포 단계에서 수행합니다. 복사·교체·설정 덮어쓰기를 자동 수행하는 설치기는 없습니다.
같은 runtime 폴더의 inference-stop.signal에 기록된 자식 PID를 쓰면 해당 실행만 종료합니다.
오래된 PID 신호는 무시합니다. 단순 kill 대신 이 신호로 수집·전송을 먼저 정리합니다.

## 명시적 continuous 선택과 탐지 범위

단일 프로필은 `runner=continuous`, `camera_ids=[PRIMARY]`, `primary_camera_id=PRIMARY`,
`continuous_config=절대 프로필 경로`, mode=review/events로 지정합니다.
단일 프로필은 PRIMARY만 탐지합니다. 프로필의 clocks에 있는 다른 카메라는 필수 증거 참고 카메라이며
추가 탐지 primary로 표시하지 않습니다. 프로필에 검증된 clock calibration과 명시적 처리/보관 예산이 없으면 실패합니다.
모델 IO가 같다는 이유로 0 오차 보정·무기한 보관·프로덕션 프로필을 생성하지 않습니다.

여러 카메라를 각각 탐지하려면 다음과 같이 각 primary에 별도 프로필을 연결합니다.

```json
{
  "camera_ids": ["CAM_A", "CAM_B"],
  "model_path": "C:\\Models\\best.pt",
  "mode": "review",
  "runner": "continuous",
  "continuous_profiles": {
    "CAM_A": "C:\\Private\\profile-a.json",
    "CAM_B": "C:\\Private\\profile-b.json"
  }
}
```

CLI 동등 옵션은 `--runner continuous --camera-id CAM_A --camera-id CAM_B
--continuous-profile CAM_A=C:\Private\profile-a.json --continuous-profile CAM_B=C:\Private\profile-b.json`입니다.
각 프로필은 해당 primary와 전체 필수 참고 snapshot을 갖습니다. 하나의 상태 디렉터리를 공유하거나
서로 포함하는 상태 디렉터리, 중복 primary, primary 선택과 다른 camera_ids는 거절합니다.
각 primary당 자식 하나를 실행하며 legacy reference_collector를 함께 실행하지 않습니다.
프로필 간 참고 영상 읽기는 겹칠 수 있지만 HTTP 읽기·디코더·버퍼·spool 비용이 primary 수에 따라 늘어납니다.

chunk 목록/다운로드는 비파괴 읽기이며 reader cursor는 개별입니다. InferenceLoad는 GPU 우선순위 신호이고
카메라 독점 lease가 아닙니다. CaptureOutbox는 상태 journal의 소유권을 잠급니다.
supervisor는 서버 주소+탐지 primary에 대해 같은 호스트/사용자의 로컬 파일 잠금을 추가합니다.
기본 잠금 위치는 OS 임시 폴더의 kyo-inference-owners이며 checkout 간 중복 실행을 차단합니다.
이는 분산 환경의 카메라 독점 보장이 아니며 다른 호스트의 실행을 차단한다고 주장하지 않습니다.

continuous 프로세스는 기본 자동 재시작 0회입니다. 새 프로세스가 진행 중 사람 ID를 복원할 수 없기 때문입니다.
한 profile이 종료되어도 다른 primary 프로세스는 유지합니다. 모든 primary 종료 또는 전파된 전역 인증 오류는 전체 종료합니다.
참고 카메라 고장으로 인한 증거 incomplete 처리·수집 종료·재시작 이력 복구는 별도 event runtime 구현을 사용하며
필수 snapshot을 줄여 증거를 완결 처리하지 않습니다. 이 패키지의 모의 검사로 실제 영상 완결성을 주장하지 않습니다.

## 초기화와 프로세스 정리

`--preflight-timeout`은 기본 120초의 유한한 초기화 예산입니다. cold-start 실측 보정값은 아닙니다.
프로필/모델/추적/PPE import/인증 사전검사는 단명 프로세스 안에서 실행합니다.
외부 stop 또는 Ctrl+C는 대기를 취소합니다. 기한 초과는 추론 자식 없이 명확히 실패합니다.
런타임 생성 임시 영상은 supervisor 전용 임시 폴더에 두고 외부 stop 파일 폴더와 분리합니다.

실제 CLI는 프로세스 트리를 소유합니다. Windows에서는 pipe gate가 열린 뒤에만 실제 명령을 시작하여
Job Object 할당 전에 encoder 자식이 생성되지 않게 합니다. Job은 kill-on-close이며 부모가 먼저 죽어도
남은 encoder를 제거합니다. POSIX에서는 독립 session/process group을 사용합니다.
정상 stop은 먼저 공유 신호로 정리 시간을 주고, 응답하지 않는 트리를 최종 제거합니다.
이 정리는 자기 소유의 job/group만 대상으로 하며 프로세스 이름으로 전체 Python/FFmpeg를 종료하지 않습니다.

reference_collector의 401/403은 이제 per-request와 최상위 poll 양쪽에서 전파되어 supervisor의 exit78에 도달합니다.
404/409 요청 충돌·429/5xx/전송 오류는 기존 보류/재시도 규칙을 유지합니다.
continuous 중첩 chunk 인증 오류의 타입 보존은 event/source 담당 구현에 의존하며 통합 검사가 필요합니다.

## 선택한 Python 환경 준비

```powershell
python -m deploy.host_runtime.prepare --python C:\PythonEnv\Scripts\python.exe
python -m deploy.host_runtime.prepare --python C:\PythonEnv\Scripts\python.exe --apply
```

첫 명령은 지정한 interpreter의 버전/import를 확인합니다. 두 번째만 해당 환경에 pip 설치를 수행합니다.
추론 requirements 및 pinned Ultralytics/lap/SciPy와 httpx가 포함된 deploy/host_runtime/requirements.txt를 사용합니다.
모델을 로드하거나 내려받지 않고 PersonTracker/빈 PPE 판정 import를 검사합니다.
검사에서는 node token/server 환경을 제거하고 라이브러리 설정 cache는 임시 폴더로 분리합니다.
기한 초과 시 준비 자식 트리도 정리합니다. 이 작업에서 --apply나 운영 dependency check를 실제로 실행하지 않았습니다.

## 검사와 한계

검사 명령은 reports/20261115_runtime-launch-handoff.md에 기록합니다.
실제 Windows 합성 자식으로 초기화 기한 초과/외부 stop/부모 급종료 뒤 손자 정리와 profile 격리를 확인합니다.
실제 PowerShell wrapper는 합성 source의 Python exit17과 stdout/stderr 및 공백·& 경로 인자를 검증합니다.
실카메라/GPU/운영 설정/DB를 사용하지 않습니다. POSIX 구현은 로컬 Windows 결과로 통과를 주장하지 않으며
통합 Linux 검사에서 별도 검증해야 합니다. 모델별 정확도, 카메라 동기화와 장시간 처리량·실영상 수동 비교는 이후 검증입니다.
자동 확정은 OFF이며 원격 main 병합·배포·GitHub 이슈 종료는 수행하지 않습니다.
