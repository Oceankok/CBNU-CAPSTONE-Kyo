# 네이티브 GPU worker — #129

## 구현 범위

컨테이너 API는 원본·미디어·작업·보관/삭제 상태를 관리하고, 서버 PC의 네이티브 worker는 인증된 HTTP로 원본을 받아 기존 YuNet/SCRFD 비식별화 함수를 실행합니다. DB·Docker volume을 공유하지 않습니다. 이벤트 이미지·클립이 대상이며, PPE 영상 송신·추론·이벤트 생성은 통합 담당 범위입니다.

SQLite·PostgreSQL v8에 processing_worker·processing_job과 미디어 generation을 추가했습니다. PostgreSQL의 v7→v8 업그레이드를 지원합니다. 실제 개발 DB 업그레이드는 아직 수행하지 않았습니다.

## 설정 — 실행은 승인 후

컨테이너 .env에서 `PPE_PROCESSING_MODE=remote`를 설정합니다. 기존 disabled 모드는 503 차단을 유지하고 기본 네이티브 local 모드는 기존 처리를 유지합니다. 장비 후보 VLM 분석은 별도 연결 전까지 컨테이너에서 503입니다.

1. DB 백업 후 기존 초기화/마이그레이션 절차로 v8을 반영합니다. SQLite seed 재삽입이 필요한지 확인하고 기존 DB에는 apply_migrations 경로를 사용합니다.
2. 관리자 인증으로 기존 `POST /api/nodes`에서 처리 PC node를 등록하고 반환 token을 안전하게 저장합니다.
3. `PUT /api/processing/workers/{node_id}`에 `{ "enabled": true }`를 보내 처리 권한을 부여합니다. 일반 영상·방송 node에는 부여하지 않습니다.
4. 서버 PC 시스템 Python에 기존 GPU 의존성과 얼굴 모델을 준비합니다. 서버 PC의 프로그램은 컨테이너의 Python 환경과 별개입니다.
5. 아래 환경변수와 네이티브 worker를 실행합니다. 실제 token은 Git·화면 캡처·이슈·문서에 넣지 않습니다.

```powershell
Set-Location C:\Users\robin\Projects\CBNU-CAPSTONE-Kyo
$env:PPE_SERVER_URL = 'http://127.0.0.1:8000'
$workerToken = Read-Host '발급받은 처리 node token' -AsSecureString
$env:PPE_PROCESSING_NODE_TOKEN = [System.Net.NetworkCredential]::new('', $workerToken).Password
Remove-Variable workerToken
python -m backend.processing.worker
```

HTTPS 공개 주소도 지원하며 origin만 설정합니다. 원격 HTTP는 허용하지 않습니다. 인증서 검증을 끄거나 redirect를 따라 다른 주소로 원본을 전달하지 않습니다. `--once`는 대기 작업 한 건 처리 후 종료합니다. 방송 PC의 Windows TTS 프로그램과 별도 프로그램입니다.

worker는 서버 주소·token 해시로 구분한 `.private_media/worker_jobs/` 하위 폴더를 사용합니다. 원본·결과는 정상/실패 종료 후 삭제하고, 서버의 cleanup 확인 전까지 작은 journal만 남겨 재전송합니다. 같은 worker의 중복 실행은 lock으로 차단합니다. 강제 종료 후에는 해당 프로세스가 없는지 확인한 다음 해당 worker.lock만 제거하고 다시 실행합니다. 남은 작업 파일은 재시작 시 정리·보고합니다.

## API 계약

관리자: worker 권한 PUT·목록 GET, `/api/processing/jobs/{job_id}` 상태 GET을 제공합니다. worker API는 node Bearer 인증·처리 권한·할당 소유자를 확인합니다. source·lease·result·failure는 `X-Processing-Lease`를 추가로 요구하며 DB에는 해시만 저장합니다.

- `POST /api/processing/claim`: 대기 작업 한 건. 없으면 job=null입니다.
- `POST /api/processing/jobs/{id}/lease`: 기본 90초 lease, worker는 30초마다 갱신합니다. 원본 만료와 최대 30분 실행 상한을 넘지 않습니다.
- `GET /api/processing/jobs/{id}/source`: 비공개 원본입니다. 관리자·일반 현장 node는 접근할 수 없습니다.
- `POST /api/processing/jobs/{id}/result`: **JPEG/MP4 바이너리 본문**과 `X-Processing-Metadata` JSON 헤더를 사용합니다. multipart 추가 의존성 없이 전송하도록 설계안에서 단순화했습니다.
- metadata는 faces_detected, processed_frames, redaction_mode, inference_backend, processing_seconds입니다. 다른 필드·잘못된 범위·깨진 결과를 거절합니다.
- `POST /api/processing/jobs/{id}/failure`: 허용된 오류 코드만 저장합니다. 내부 traceback·경로는 저장하지 않습니다.
- `POST /api/processing/jobs/{id}/cleanup`: 할당된 node가 로컬 사본 정리를 확인합니다. processing 권한을 해제한 후에도 node가 활성 상태이면 정리 보고만 가능합니다.

결과는 비공개 수신 → 디코딩·프레임 수 검증 → 소유자/lease/세대/보관 상태 재검사 → canonical 경로 게시 순서입니다. 파일 검사만으로 실제 모자이크 품질을 판정하지 않으며 등록된 worker의 비식별화 코드를 신뢰합니다. 같은 해시의 성공 결과 재전송은 중복 게시하지 않습니다.

## 프론트 계약

remote 업로드는 처리 대기 미디어를 201로 반환하며, 재처리는 202로 반환합니다. `job_id`·`processing_state`를 추가합니다. `status=ready`와 URL 제공 전에는 처리 완료가 아닙니다. 대기/실행 중에는 can_reprocess=false입니다. 프론트 화면은 수정하지 않았습니다.

기존 이벤트 미디어 GET으로 처리 상태를 갱신합니다. 삭제·작업 만료 후 늦은 결과는 409이며 게시하지 않습니다. 원본 소실·손상에 따른 기존 검토 제한을 유지합니다.

## 장애·삭제

lease 만료는 실패이며 자동 재할당하지 않습니다. 관리자가 재처리하면 새 job_id·generation을 사용합니다. 이전 작업의 늦은 결과는 거절합니다. worker는 이전 작업의 정리 확인을 마쳐야 새 작업을 받습니다.

이벤트 삭제/보관 만료는 queued/running 작업을 취소하고 원본 조회·결과 게시를 차단합니다. 이미 내려받은 사본이 있으면 cleanup 확인 전 삭제 완료를 표시하지 않습니다. node 오프라인 또는 강제 종료 시에는 대기 상태로 남으며 재시작 후 정리 보고를 받습니다. node 비활성화로 보고가 막힌 경우에는 관리자가 복구·정리를 확인해야 합니다.

원본 TTL 만료 작업은 새로 할당하지 않습니다. maintenance는 원격 작업 lease를 기준으로 중단을 판단하고 기존 로컬 작업 timeout과 구분합니다.

## 검증 계획

- 격리 SQLite·PostgreSQL DB와 가짜 GPU로 대기·claim·원본 접근·완료·중복 결과를 확인합니다.
- 일반 node 권한 거부·원본 소실·손상 결과·lease 만료·재처리 세대·삭제 후 결과 차단·cleanup 대기를 확인합니다.
- 네이티브 worker를 가짜 HTTP/GPU로 연결하여 파일 정리·서버 확인을 검증합니다.
- 실제 Docker API↔호스트 GPU 이미지·영상 검증은 별도 승인 후 수행합니다.

2026-10-12 승인 후 새 작업 테스트 7개를 통과했습니다. 전체 회귀는 SQLite·PostgreSQL 각각 83개 중 79개가 통과하고 DB 종류별 전용 테스트 4개를 제외했습니다. PostgreSQL v7→v8 업그레이드를 포함합니다. 마지막 미정리 작업 수신 차단 보완 이후에는 관련 7개를 두 DB에서 재검증했습니다.

테스트는 임시 DB·파일·가짜 GPU와 HTTP 응답을 사용했으며 실제 개발 DB·모델·방송·외부 VLM은 사용하지 않았습니다. 테스트용 PostgreSQL 컨테이너·volume은 정리했습니다. 실제 Docker API와 네이티브 GPU 모델 간 이미지·영상 검증은 아직 수행하지 않았습니다.

선행 PR #130·#131과 PostgreSQL 전환 변경을 기준으로 작성한 후속 브랜치입니다.
