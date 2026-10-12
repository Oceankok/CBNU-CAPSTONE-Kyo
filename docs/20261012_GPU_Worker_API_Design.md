# 호스트 GPU worker 연동 설계 — #129 후속

이 문서는 구현 전 API·DB 계약 초안입니다. 엔드포인트·테이블·worker 프로그램은 아직 추가하지 않았습니다. PostgreSQL 작업 브랜치를 기반으로 작성했으며 실제 DB·서버·프론트는 변경하지 않았습니다.

## 1. 역할 및 첫 구현 범위

컨테이너 API는 원본 접수·작업 등록·인증·결과 게시·삭제를 담당합니다. 서버 PC의 네이티브 worker는 원본을 인증된 API로 내려받아 기존 `redact_image`·`redact_video`를 실행하고 결과를 업로드합니다. worker는 DB나 Docker volume에 직접 접근하지 않습니다.

첫 구현은 **이벤트 이미지·클립 비식별화**에 한정합니다. 영상 송신·PPE 추론·이벤트 생성 연결은 AI·통합 담당 범위입니다. 장비 후보 분석은 원본 비식별화 이후 VLM 요청까지 별도 연결이 필요하므로, 이번 worker만으로 활성화하지 않습니다. 컨테이너의 장비 분석 503 제한은 후속 연결 전까지 유지합니다.

작업 방식은 HTTP polling이며 worker는 한 번에 한 작업만 실행합니다. Redis·Celery·공유 파일 경로·다중 GPU 스케줄링은 도입하지 않습니다. API에 업로드된 원본은 공개 URL로 제공하지 않습니다.

## 2. 기존 코드에서 바뀌는 경계

현재 `ingest_file`·`reprocess_media`는 API 호출 안에서 GPU 처리 후 `_publish`로 게시합니다. `register_media`가 곧바로 processing 상태를 만들고 `processing_started_at`을 설정합니다. maintenance는 해당 시각으로 중단 작업을 판단합니다.

원격 처리 모드에서는 접수·원본 보존·작업 등록을 먼저 끝내고 비식별화 결과는 나중에 게시합니다. 로컬 처리 경로는 당분간 유지하되 실행 모드를 명시적으로 선택합니다.

`PPE_PROCESSING_MODE=local|remote|disabled`를 도입하고, 새 설정이 없으면 기존 `PPE_LOCAL_PROCESSING`과 호환합니다. remote에서는 worker가 오프라인이어도 유효한 업로드를 대기열에 접수하고 현재 상태를 보여줍니다. 모델 미설치·worker 장애 시 원본 공개나 CPU 자동 대체를 하지 않습니다.

## 3. DB 설계

다음 마이그레이션에서 아래 구조를 추가합니다. 실제 버전 번호는 병합 시 최신 버전을 확인하여 결정합니다.

### processing_worker

- `node_id`: field_node FK·PK. 기존 node 토큰·활성 상태·heartbeat를 재사용합니다.
- `enabled`: 관리자만 변경하는 처리 권한입니다.
- 등록된 일반 현장 node는 처리 권한이 없으므로 원본·GPU 작업 API에 접근하지 못합니다.

### processing_job

- `job_id`: UUID PK, `media_id`: event_media FK
- `generation`: 미디어 처리 세대, `parent_job_id`: 재시도 이전 작업 FK·nullable
- `status`: queued / running / succeeded / failed / cancelled
- `redaction_mode`: enhanced 또는 scrfd, `kind`: image 또는 video
- `worker_node_id`: 할당 worker·nullable
- `lease_token_hash`, `lease_until`, `started_at`, `finished_at`
- `error_code`, `created_at`, `updated_at`
- `local_cleanup_status`: not_required / pending / confirmed
- `result_sha256`: 성공 결과 식별용·nullable

한 작업 ID는 한 번의 실행에 대응합니다. 재시도는 새 작업 ID를 생성하여 기존 worker의 원본 사본·실패·정리 기록을 덮어쓰지 않습니다. 같은 미디어의 queued/running 작업은 최대 한 건으로 제한합니다. 작업 claim과 상태 갱신은 기존 `begin_write` 트랜잭션 경계를 사용합니다.

event_media에는 `processing_generation`을 추가합니다. 새 작업 등록 시 증가시키며 늦게 도착한 이전 세대 결과를 거절합니다. 작업 대기는 기존 media `status=processing`으로 표시하고 `processing_started_at`은 실제 claim 때 설정합니다. public_media에 `processing_state=queued|running|failed|null`을 추가합니다. queued/running에서는 `can_reprocess=false`입니다.

## 4. 관리자 및 기존 미디어 계약

| API | 제안 |
|---|---|
| `PUT /api/processing/workers/{node_id}` | `{ "enabled": true }`로 처리 권한 지정. 관리자 전용 |
| `GET /api/processing/workers` | 설치된 worker의 등록·활성·last_seen 상태 조회. 토큰 미노출 |
| `GET /api/processing/jobs/{job_id}` | 관리자용 상태 조회. 원본 경로·lease token 미노출 |
| 기존 `POST /api/events/{event_id}/media` | remote에서 파일 접수·작업 등록 후 201. 미디어에 processing_state/job_id 추가 |
| 기존 `POST /api/media/{media_id}/redact` | remote에서 새 재처리 작업 등록 후 202. 실행 중이면 409 |
| 기존 `GET /api/events/{event_id}/media` | URL이 없는 처리 중 미디어·작업 상태 조회 |

파일 형식·업로드 상한·미디어 역할·이벤트/카메라·보관 삭제 상태는 접수 전에 검증합니다. 업로드와 DB 사이 실패는 임시 원본을 정리하며, 성공적으로 보존한 원본과 DB 작업이 연결된 경우만 접수를 완료합니다.

**프론트 합의가 필요합니다.** 현재 업로드 응답을 처리 완료로 취급하는 화면은 remote에서 대기 상태를 표시하고 기존 조회 API로 완료를 갱신해야 합니다. 201은 접수이며 `status=ready`와 URL 제공이 완료입니다. 실패하면 원인과 재처리 가능 여부를 표시합니다. 화면 변경은 프론트 담당 PR로 진행합니다. 원본이 없거나 손상된 경우의 검토 제한은 유지합니다.

## 5. worker 전용 API

모든 API는 등록 node Bearer token과 processing_worker 권한을 검사합니다. 해당 worker에 할당된 작업만 접근할 수 있습니다. 원본·결과·갱신에는 추가 `X-Processing-Lease` 헤더를 요구하고 서버는 해시로 비교합니다. 토큰은 URL·로그·DB 평문에 넣지 않습니다.

| API | 요청/응답 |
|---|---|
| `POST /api/processing/claim` | 대기 작업 한 건 원자적 할당. 없으면 `{ "job": null }` |
| `POST /api/processing/jobs/{job_id}/lease` | 유효한 실행 소유자가 lease 갱신. 취소·만료·이전 세대는 409 |
| `GET /api/processing/jobs/{job_id}/source` | 해당 작업의 비공개 원본 스트리밍. 다운로드 직전 보관 상태·원본 만료·lease 재검사 |
| `POST /api/processing/jobs/{job_id}/result` | multipart: 결과 파일과 JSON 처리 메타데이터. 서버 검증·게시 후 job 상태 반환 |
| `POST /api/processing/jobs/{job_id}/failure` | 허용된 error_code와 로컬 파일 정리 여부 보고 |
| `POST /api/processing/jobs/{job_id}/cleanup` | 취소·만료 작업의 로컬 원본/결과 사본 정리 확인. 과거 소유자도 정리 보고만 가능 |

claim 응답은 job_id·media_id·generation·kind·mode·lease 만료·일회 생성 lease token·동일 API origin의 source 경로를 제공합니다. 임의의 다운로드 URL·파일 시스템 경로는 제공하지 않습니다.

worker는 서버 origin을 설정에서 고정하고 redirect를 따라 원본을 다른 호스트로 보내지 않습니다. 결과 메타데이터는 faces_detected·processed_frames·redaction_mode·inference_backend·processing_seconds를 허용하며 파일 경로나 임의 DB 필드는 받지 않습니다.

## 6. 결과 게시 및 중복 처리

결과는 먼저 비공개 임시 폴더에 수신합니다. 파일 형식·크기·디코딩 가능 여부와 메타데이터의 타입·범위를 검증합니다. 영상의 전체 디코딩·길이/프레임 검증과 기존 재인코딩 정책을 재사용합니다. 이 검사만으로 얼굴 비식별화 품질을 증명할 수는 없으며 등록된 worker의 처리 파이프라인을 신뢰하는 구조입니다.

게시 트랜잭션에서 job의 소유자·lease·generation·running 상태, 미디어 상태와 보관 결정을 다시 확인합니다. 유효한 경우만 서버가 생성한 canonical 경로로 파일을 이동하고 media를 ready/complete, job을 succeeded로 기록합니다. 원본·worker가 보낸 임의 경로를 storage_path로 사용하지 않습니다.

같은 완료 작업에 동일 파일 해시의 결과를 재전송하면 저장·게시를 반복하지 않고 기존 성공을 반환합니다. 다른 파일·잘못된 소유자·취소·만료 결과는 409이며 수신 임시 파일을 제거합니다. 파일 이동 후 DB 실패가 발생하면 orphan 정리 대상으로 기록/탐지합니다.

## 7. 장애·재시도·삭제

- 권장 기본값: polling 2초, lease 90초, 갱신 30초, 단일 작업 최대 30분. 실제 클립 길이·추론 부하 검증 후 조정합니다.
- worker는 처리 중 별도 갱신 루프를 실행합니다. lease 갱신 실패·취소 시 결과를 게시하지 않고 로컬 사본을 정리합니다.
- lease 만료는 실행 실패로 기록하되 **실제 로컬 사본 삭제로 간주하지 않습니다.** 소유자와 cleanup pending 기록을 유지합니다.
- 첫 단계에서는 자동 재할당하지 않고 관리자 재처리를 사용합니다. 정상 worker가 보고한 일시 실패의 제한적 자동 재시도는 후속으로 검토합니다.
- worker는 전용 작업 폴더와 작은 실행 journal을 사용하고 정상 종료·실패의 finally에서 정리합니다. 강제 종료 잔여물은 재시작 시 정리하고 cleanup 보고를 재전송합니다.
- 이벤트 삭제/보관 만료 시 작업을 cancelled로 전환하고 이후 원본 다운로드·결과 게시·lease 갱신을 차단합니다. 서버의 원본·결과·임시 수신 파일도 정리합니다.
- worker가 이미 내려받은 사본이 있으면 local_cleanup_status 확인 전 전체 삭제 완료로 표시하지 않습니다. 오프라인이면 삭제 대기 상태를 유지하고, 재연결 시 정리 확인을 받습니다. 기존 media 삭제와 작업 정리를 함께 검사하여 retention.deleted_at을 결정합니다.

## 8. maintenance 변경

작업 대기를 processing_started_at 기준의 중단 작업으로 오판하지 않도록 원격 job과 기존 로컬 작업을 구분합니다. running의 lease 만료를 먼저 처리하고, 활성 lease의 원본·수신 결과를 orphan으로 삭제하지 않습니다. 원본 TTL을 넘긴 작업은 새 claim을 금지하며 무한히 보관 기간을 연장하지 않습니다.

기존 사건 보관 만료·삭제 재시도·후보 근거 정리는 유지합니다. job 임시 파일과 worker 정리 미확인 상태를 추가로 추적합니다.

## 9. 구현 순서 및 검증

1. API 응답 변경·에러 코드와 프론트 갱신 계약을 공유합니다.
2. 처리 권한·job·generation·cleanup DB 마이그레이션과 작업 서비스부터 구현합니다.
3. 비공개 원본 접수·claim·lease·파일 전달·결과 게시·삭제 연동을 추가합니다.
4. 기존 비식별화 함수를 호출하는 네이티브 worker와 실행 안내를 추가합니다.
5. maintenance를 작업·lease 기준으로 확장합니다.
6. 승인 후 가짜 GPU로 queued → running → ready, 권한 거부·중복 결과·이전 세대·lease 만료·취소·정리 미확인·삭제 경쟁 조건을 검증합니다.
7. 별도 승인 후 실제 모델로 이미지·짧은 영상 한 건씩 Docker API ↔ 네이티브 worker를 검증합니다.

웹 업로드·재처리의 비동기 갱신, 미디어 오류 안내는 프론트 담당과 합의 후 연결합니다. PPE 추론 우선순위·동시 카메라 수·실시간 영상 전송 프로토콜은 AI·통합 담당과 별도로 결정합니다.
