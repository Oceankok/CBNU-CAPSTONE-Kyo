# Docker API와 네이티브 GPU worker 실연동 검증

관련 이슈: #137, #151, #155

## 범위

`deploy/tests/validate_gpu_worker.py`는 게시된 API 이미지와 별도 PostgreSQL·미디어 volume을 실행하고, 호스트 Python의 실제 GPU worker를 HTTP로 연결하는 수동 실행 검증 도구입니다. 운영 `kyo-server`의 계정·DB·설정·처리 모드를 변경하지 않습니다.

기존 격리 단위 테스트는 가짜 GPU 결과를 사용합니다. 이 도구는 실제 YuNet(`enhanced`)·SCRFD(`scrfd`) 모델과 ONNX Runtime CUDA provider를 사용하며 CPU 대체를 통과로 인정하지 않습니다. GPU가 없는 일반 CI나 Mac 개발 환경에서 자동 실행하지 않습니다.

입력은 인물이 없는 320×240 합성 JPEG와 12fps·12프레임 영상입니다. 외부 이미지·영상이나 상용 VLM을 사용하지 않습니다. **연동·실행 경로 검증이며 얼굴 탐지 정확도·비식별화 품질·실시간 PPE 성능을 평가하지 않습니다.**

## 사전 준비와 실행

- Docker Linux 엔진, 이미 게시된 API image digest가 필요합니다. 로컬 소스를 자동 빌드하지 않습니다.
- 호스트 Python에 기존 GPU 미디어 의존성, OpenCV·HTTPX·NumPy와 영상 인코더가 설치되어 있어야 합니다. 스크립트가 패키지를 설치하지 않습니다.
- 기존 YuNet·SCRFD 모델이 프로젝트 모델 설정 경로에 있어야 합니다. 모델의 다운로드·라이선스는 기존 미디어 설정 문서를 따릅니다.
- 실행 전 GPU 사용과 테스트용 컨테이너·DB 생성 승인을 받습니다. 기존 PPE 추론과 성능 경쟁을 피할 수 있는 시점에 진행합니다.

프로젝트 루트 PowerShell에서 실행합니다.

```powershell
$release = Get-Content (Join-Path $env:USERPROFILE 'KyoServer/current-release.json') -Raw | ConvertFrom-Json
python deploy/tests/validate_gpu_worker.py --api-image $release.api_image
```

현재 배포 기록이 없다면 Actions의 `release-references`에 있는 실제 `ghcr.io/...@sha256:...` API 주소를 인자로 지정합니다. registry 인증이 필요한 경우에도 비밀번호나 토큰을 인자로 전달하지 않습니다.

## 확인하는 흐름

1. 무작위 프로젝트의 PostgreSQL을 초기화하고 일회성 관리자·처리 node를 생성합니다. 자격 증명은 출력하거나 결과 파일에 기록하지 않습니다.
2. 이미지의 관리자 업로드와 영상의 처리 node 클립 요청·접수를 실행합니다. 대기 자료는 공개 URL이 없어야 하며 관리자 token이나 미인증 요청으로 worker 원본을 읽을 수 없어야 합니다.
3. 추론 우선 신호가 busy인 동안 작업 claim이 보류되고 해제 후 진행되는지 확인합니다. PPE 추론 자체는 실행하지 않습니다.
4. 호스트 `Worker`가 원본을 HTTP로 받아 실제 CUDA 처리 후 결과를 반환합니다. 호스트 worker는 PostgreSQL이나 컨테이너 DB volume을 직접 읽지 않습니다.
5. 두 모델의 이미지·영상 총 4개 조합이 `ready`·`complete`·`onnxruntime-cuda`가 되는지 확인합니다. 영상은 클립 요청이 `received`로 연결되고 게시 결과의 12프레임이 모두 디코딩되어야 합니다.
6. worker 로컬 payload·journal 삭제와 서버의 cleanup 확인 상태를 검사합니다. 서버의 재처리용 비공개 원본은 테스트 volume 정리 시 함께 제거합니다. 운영 원본 TTL 만료 검증과는 구분합니다.
7. 테스트 컨테이너·volume과 호스트 임시 파일을 정리합니다. `kyo-server`는 정리 대상이 아닙니다.

성공 결과는 Git에서 제외된 `.private_media/gpu-worker-smoke-results.json`에 저장합니다. 표기된 처리 시간은 합성 자료·초기화·캐시 상태에 영향을 받으므로 모델 속도 비교 수치로 사용하지 않습니다. 강제 프로세스 종료 시 자동 정리가 실행되지 않을 수 있으므로 해당 `kyo-gpu-smoke-*` 프로젝트를 확인합니다.

## 이후 남은 작업

- 재부팅 후 worker 재시작 및 로그온 자동 실행 설정 정리
- AI 담당의 PPE 추론·이벤트 생성·전후 클립 생성과 연결
- 실제 현장 PC·다중 카메라·방송 및 관리자 검토 화면까지 통합 검증
- 실제 인물 자료의 얼굴 비식별화 품질 검토와 동시 부하·지연 측정

이 검증 통과만으로 위 항목 전체를 완료 처리하지 않습니다.

2026년 10월 20일 서버 PC에서 4개 조합을 실제 CUDA로 실행해 모두 통과했습니다. 영상 클립 요청·접수, 추론 우선 보류, worker 원본 접근 제한, 게시 영상 프레임 보존과 로컬 cleanup 확인을 포함하며 테스트 컨테이너·volume은 정리했습니다.

## 실제 서버 연결 상태 — 2026년 10월 20일

관리자 인증으로 `server-gpu` node와 처리 권한을 등록하고 네이티브 worker를 백그라운드에서 실행했습니다. node token은 배포 폴더에 현재 Windows 계정으로 암호화하여 저장하고 process 환경을 통해 전달합니다. 다른 계정·PC로 파일을 복사하는 방식으로 자격 증명을 공유하지 않습니다.

기존 서버의 `PPE_PROCESSING_MODE`를 `remote`로 전환하고 API·웹·maintenance를 재생성했습니다. 처리 node 권한·새 heartbeat·readiness와 중복 실행 방지를 확인했습니다. 기존 계정과 PostgreSQL 데이터는 유지했습니다. 존재하지 않는 이벤트의 클립 접수 요청이 처리 모드 차단 503 대신 404로 응답하는 것을 확인했으며 실제 사건을 생성하지 않았습니다.

로컬 시작 파일은 배포 폴더의 `Start-Worker.ps1`입니다. 현재는 백그라운드 프로세스이며 Windows 서비스·재부팅 후 자동 시작은 설정하지 않았습니다. 강제 종료나 재부팅 후 `worker.lock`이 남으면 기존 프로세스가 종료됐는지 확인한 뒤 재시작해야 합니다.

실제 배포 서버에서 사건 미디어 작업을 끝까지 수행하는 검증은 별도 자료·이벤트를 준비한 뒤 진행합니다. 위의 4개 CUDA 사례는 운영 서버와 분리된 환경의 검증 결과입니다.
