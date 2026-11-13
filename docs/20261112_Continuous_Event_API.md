# 연속 사건 API 기반 (실험적, 기본 비활성)

## 범위와 금지

이 변경은 한 사람·카메라·PPE·정책 범위의 사건을 유지하고 여러 영상 조각을 연결하는 서버 기반이다. 탐지 모델과 프론트는 변경하지 않는다. 자동 판정 기능은 구현·활성화하지 않는다. 사용자 정책은 **3초 관측 시 사건 및 예방방송, 종료 이후 전체 구간을 기준으로 자동판정, 필요시 관리자 재처리**이다. 자동판정은 정확도 검증 전 OFF다.

`PPE_CONTINUOUS_EVENTS_ENABLED=true`가 명시되어야 새 수신 API를 사용한다. 기본은 503 `continuous_events_disabled`이다. 테스트만 완료한 기반으로, runner/outbox/실카메라 검증 및 아래 보류 정책이 해결되기 전 운영에서 켜지 않는다.

기존 `/api/processing/events`는 불변·단발 계약 그대로다. 기존 사건은 lifecycle에 자동 편입하지 않는다. 스키마 v23/v24는 신규 테이블만 추가한다. v21/v22 자료 이관은 이전 백엔드 수정의 사용자 승인 범위로 별개다.

## 인증 및 사건 시작

모든 아래 수신 API는 기존 활성 processing worker 인증을 사용한다. 최초 start를 성공시킨 node ID와 session ID를 소유자로 고정한다. 카메라 송신 source node와 추론 processor는 다른 역할일 수 있으므로 이를 동일 계정으로 간주하지 않는다. 다른 processor는 같은 event ID를 알아도 갱신할 수 없다. session ID는 권한 토큰이 아니며 node 인증이 함께 필요하다.

`POST /api/processing/continuous-events`

```json
{
  "event_id": "EVT_0123456789abcdef0123456789abcdef",
  "camera_id": "CAM_001",
  "tracking_id": "track-12",
  "ppe_type": "helmet",
  "timestamp_start": "2026-11-12T00:00:00Z",
  "timestamp_end": "2026-11-12T00:00:03Z",
  "confidence": null,
  "frame_sample_count": 46,
  "model_version": "current-model",
  "review_only": true,
  "session_id": "0123456789abcdef0123456789abcdef",
  "policy_key": "camera-policy-revision-1",
  "observed_seconds": 3,
  "gap_seconds": 0,
  "gap_count": 0
}
```

- start의 span 및 observed_seconds 모두 최소 3초. camera/track/PPE/policy/session을 다른 사람에게 재사용하면 안 된다.
- 최초 201, 동일 body 재시도 200. 다른 body로 ID 재사용 409.
- 기존 구역 snapshot·retention 생성은 유지. 그룹 참고카메라는 시작 시 snapshot으로 고정.
- 기존 고정길이 clip request 대신 manifest 등록 시 primary/reference 조각 요청을 함께 만든다.
- 방송은 기존 enable·온라인·cooldown·event 중복방지 규칙을 적용한다. progress/close에서 방송을 재요청하지 않는다.

## 진행과 종료

`POST /api/processing/continuous-events/{event_id}/updates`

```json
{
  "session_id": "0123456789abcdef0123456789abcdef",
  "sequence": 1,
  "kind": "progress",
  "timestamp_end": "2026-11-12T00:01:00Z",
  "frame_sample_count": 901,
  "confidence": null,
  "observed_seconds": 59.8,
  "gap_seconds": 0.2,
  "gap_count": 1
}
```

- update sequence는 1부터 빈 번호 없이 증가. start/progress/close는 durable outbox에 보관한 뒤 보내고 ack 후 제거해야 한다. 메모리 재시도만으로는 재시작 손실을 막지 못한다.
- 같은 sequence 같은 내용은 200, 다른 내용은 409. 응답은 현재 서버 상태이므로 오래된 재시도 응답의 revision은 요청 sequence보다 높을 수 있다.
- 시간·프레임수·관측량은 감소할 수 없다. gap은 관측된 위반시간으로 표시하면 안 된다.
- review_only는 confidence=null. verified 모드는 전체 구간의 누적 최소 confidence를 필수 제출하며 이전값보다 커질 수 없다. 이 값은 규칙 기반 자동판정 점수와 다르다.
- 현 baseline 제약인 span 최대 3600초, frame_sample_count 최대 100000을 유지한다. 초과 시 임의 새 사건 생성하지 않고 명시 오류로 남긴다. 장기 정책은 보류다.

close는 같은 endpoint에 `kind:"close"`, `close_reason`, `complete`, `final_segment_count`를 추가한다. 모든 조각 manifest를 먼저 등록한다. 완전 close는 연속 manifest가 사건 start 앞 2초~마지막 관측 뒤 2초를 포함해야 한다. complete=false는 중단(interrupted)이며 0개 manifest도 가능하다. 종료 후 신규 메타데이터나 조각 manifest는 거절하지만 이미 등록한 요청의 업로드·재처리 경로는 남는다.

## 영상 조각

`POST /api/processing/continuous-events/{event_id}/segments`

```json
{
  "session_id": "0123456789abcdef0123456789abcdef",
  "sequence": 0,
  "capture_start_at": "2026-11-11T23:59:58Z",
  "capture_end_at": "2026-11-12T00:00:58Z"
}
```

- sequence는 사건별 0부터. camera 전역 스풀 번호와 별개다.
- capture_end는 exclusive 경계. 완성 파일 최대 60초이며 앞뒤 여유영상을 포함한다.
- 다음 조각 start는 직전 end와 일치해야 한다. 동일 선언 재시도는 같은 request ID를 반환한다.
- 현재 관측범위 앞뒤 2초 밖 선언은 거절한다. 선언은 추적 상태 progress가 반영된 후 보내야 한다.
- 시작 시 snapshot된 모든 카메라에 요청을 만들며 role은 주 카메라 clip, 나머지 reference다. 업그레이드된 collector만 `GET /api/processing/continuous-events/reference-requests`를 사용한다. 기존 `/reference-clip-requests`에는 연속 사건 요청을 숨겨 max-20초인 구형 collector가 실패 처리하지 않도록 한다. 새 endpoint는 사건 소유 processing node만 조회한다.
- 만료된 보관 또는 조각 업로드 기한 이후에는 원본 수신 시작과 등록 시점 모두 거절한다. 기존 요청 조회/실패보고/업로드도 연속 사건의 소유자만 허용하고, 기존 단발 POST로 연속 사건을 재접수하면 409다.
- 비식별 처리 결과는 실제로 디코딩한 프레임 수/FPS로 길이를 검증한다. 선언 구간과 `min(실제 1프레임, 1/15초)`보다 크게 다르거나 60초를 초과하면 공개하지 않는다. 15fps 기본 clip 생성기의 1프레임을 절대 오차 상한으로 사용하여 저FPS가 수초 누락을 허용하지 않게 한다. FPS 자체의 최솟값을 새로 강제하지는 않는다. 처리 세대별 검증 기록이 있어야 전체 증거로 인정한다. 이 검사는 파일 길이의 일치이며 원본이 해당 실제 촬영 시각에 촬영됐다는 독립적 증명은 아니다.
- 응답의 request_id를 기존 `POST /api/processing/events/{id}/clip?request_id=...` 바이너리 업로드에 사용한다. media capture 구간은 조각 구간으로 저장된다.
- `PPE_CONTINUOUS_CLIP_DELIVERY_SECONDS`는 기본 300초(허용 30~3600)이며 촬영 구간과 별개 업로드 기한이다. maintenance의 기존 idle-upload 보호를 유지한다. 실제 최적값은 현장 검증 필요.

## 응답과 프론트 연결

수신 응답은 `event_id`, `created`(start만), `lifecycle`, `segments`, `auto_decision_enabled:false`를 제공한다. lifecycle에는 phase(open/closed/interrupted), revision, evidence_revision(현재는 manifest 등록 revision이며 영상 재처리 세대와 다름), 관측량/공백량, 마지막 update 시각, final_segment_count, close_reason이 있다. segments는 카메라·순번·capture 구간·request_id·role·delivery 상태를 담는다.

`GET /api/processing/continuous-events/{id}?session_id=...`는 producer 복구 조회다. 프론트는 이 worker 인증 API를 호출하면 안 된다.

기존 관리자 `GET /api/events` 및 상세 응답에 신규 사건만 `collection`과 `auto_decision_enabled:false`가 추가된다. collection으로 수집중/종료/중단을 구분한다. 기존 미디어 배열과 클립 선택은 유지한다.

서버는 open/interrupted 사건의 최종검토·hold·병합을 현재 차단하고, closed도 하나라도 미수신/미처리/실패 조각이 있으면 `continuous_evidence_incomplete`로 차단한다. 이때 review_availability.can_review/can_mark_unreviewable=false다. **부분 증거의 최종판정 정책이 정해지지 않아 보수적으로 차단하는 단계이며 자동판정이 수행된다는 뜻이 아니다.** 관리자 override API 및 자동판정 provenance·rule/evidence revision은 별도 후속 작업이다. 기존 판정조건 전체를 완성했다고 설명하면 안 된다.

## 보류·운영 활성화 조건

- 정확한 관측 공백 유예값: 사용자가 승인한 동일 track만 연결, ID 변경 새 사건 방향 외 숫자 미정.
- heartbeat/lease 만료로 사라진 producer의 open 사건 종료 기준.
- 누락·실패·interrupted 사건을 자동 또는 관리자 판정할 수 있는 기준.
- 판정/보관·철회 이후 추가 수집 중단 및 producer 정리 신호.
- 1시간/100000프레임을 넘는 사건 처리.
- 참고카메라가 늦게 요청받았을 때 장기 스풀 보유/개인정보 상한. 현재 수신버퍼는 30초이므로 60초 조각 완성 후 요청하는 방식만으로는 참고영상을 복구할 수 없다. 사건 시작 시점부터 참고카메라 스풀에 참여하는 아래 discovery와 카메라별 보고 계약을 실제 collector에 연결해야 한다. 새 reference endpoint는 구형 collector의 실패 처리를 차단할 뿐 live60초 참고 수집을 완성하지 않는다.
- 자동판정 뒤 관리자 재처리 및 자동결정이 관리자 결정을 덮어쓰지 않는 revision 계약.
- 프론트 상태·재조회·조각 번호/구간/실패 표시 연동.
- 실제 PostgreSQL 이관·동시성, 2카메라 GPU/메모리/디스크/재시작 실측.


## 참고 카메라 전체 구간 수집 (사용자 옵션 2)

참고 카메라도 시작 전 2초부터 사건 종료 후 2초까지 전체 범위를 수집한다. 늦은 60초 manifest 요청만 기다리면 기존 30초 수신버퍼로 복원할 수 없으므로, start 응답의 `cameras`를 보고 즉시 카메라별 spool을 시작해야 한다. primary와 reference 모두 동일 규칙이다.

- start/read/갱신 응답 `cameras[]`: `camera_id`, `role`, `required_start_at`, `required_end_at`, `capture_report`(최신 보고 또는 null).
- `GET /api/processing/continuous-events/active?after=<event_id>&limit=50`: 소유 processor만 조회. `items`, `next_after`를 반환한다. 항목에 `session_id`와 기존 public 응답을 포함한다. 첫 페이지부터 마지막까지 읽고 매 polling 회차 첫 페이지로 돌아가야 새 ID를 놓치지 않는다. open뿐 아니라 종료 뒤 postroll/업로드가 남을 수 있는 미검토 사건도 포함한다. 완료 항목도 검토 전까지 포함될 수 있으므로 클라이언트는 상태로 중복 수집하지 않는다. 삭제/보관만료/검토/병합 사건은 제외한다.
- 현재 **단일 소유 processor coordinator**만 지원한다. 참고 카메라 별도 node에 권한을 나눠 주는 계약은 구현하지 않았다.
- `POST /api/processing/continuous-events/{event_id}/cameras/{camera_id}/capture-reports`:

```json
{
  "session_id": "0123456789abcdef0123456789abcdef",
  "sequence": 1,
  "state": "collecting",
  "captured_ranges": [{"start_at":"2026-11-11T23:59:58Z","end_at":"2026-11-12T00:00:03Z"}],
  "missing_ranges": [],
  "earliest_available_at": "2026-11-11T23:59:58Z",
  "time_basis": "shared_monotonic",
  "clock_offset_seconds": 0,
  "clock_uncertainty_seconds": 0,
  "reason": null
}
```

카메라별 sequence 1부터 연속 증가한다. 동일 재시도 200, 내용/순번 충돌 409. snapshot에 없는 카메라 404, 다른 processor 403. 범위는 timezone을 포함하며 `[start,end)`이고 0보다 길어야 한다. `captured_ranges`는 정렬·비중첩 누적 snapshot이며 이전 범위를 없앨 수 없다. `missing_ranges`는 시각과 reason을 포함하고 후속 보고로 삭제할 수 없다. 늦은 발견·pre-roll 유실·카메라 단절·용량 제한·clock 불확실·재시작 등은 `state:incomplete` 및 reason/누락 범위로 기록한다. 과거 보고는 별도 테이블에 보존하며 자동 복구/누락 소거는 허용하지 않는다. 복구 승인 정책은 보류다.

`state:complete`는 원본 **capture 범위 완료**이지 비식별 처리 완료나 자동판정 완료가 아니다. 사건이 closed이고 모든 요구 범위를 빈틈없이 포함하며 누락이 없어야 한다. `PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS`가 명시되어야 하며 기본값은 없다. 설정 없으면 complete는 503, 보고된 uncertainty가 상한을 넘으면 422. 테스트의 0은 동일 공통시계 fixture일 뿐 운영 권장치가 아니다. time_basis는 `shared_monotonic` 또는 `source_utc_corrected`; offset은 원천 시계를 공통 기준으로 보정한 값이다. shared_monotonic도 서버에는 timezone 포함 UTC로 보내며 producer가 세션 시작 때 고정한 monotonic→UTC epoch 매핑을 사용한다. 원천 PTS를 UTC라고 해석하면 안 된다. 서버는 producer의 시계 계측을 독립 인증하지 못하므로 해당 값이 임의 주장되지 않도록 실제 coordinator/clock 검증이 필요하다.

최종검토는 모든 카메라의 complete 보고 **및** 모든 조각의 수신·비식별 처리·실제파일 길이 검증이 필요하다. 관리자 상세의 `collection.cameras`에 범위와 누락 상태를 전달한다. 기존 단발 사건 응답과 검토는 바뀌지 않는다. 서버 계약과 단위 테스트만으로 실시간 전체 카메라 수집이 완성된 것은 아니다. durable outbox, 종료 의도→postroll→manifest→HTTP close 순서, 재시작/디스크/시계/다중카메라 E2E 검증 전 운영 비활성을 유지한다.

## 원본 업로드 응답 유실 복구 receipt (v25)

`GET /api/processing/continuous-events/{event_id}/receipts/{request_id}?session_id=...`는 사건 소유 processor만 사용할 수 있는 읽기 전용 endpoint다. Bearer 인증과 session ID 모두 일치해야 한다. 응답은 `Cache-Control: private, no-store`이며 원본 경로·URL·바이트를 반환하지 않는다.

서버가 수신 파일의 SHA256과 byte size를 직접 계산한다. private source 저장과 처리 queue 등록을 마친 뒤 clip request의 received 전이와 같은 DB transaction에 불변 receipt를 저장한다. 클라이언트가 보낸 digest 주장을 그대로 기록하지 않는다. 실패·미완료 ingest에는 receipt가 없다. 재업로드는 여전히 409이며, 409만 보고 성공 처리하면 안 된다.

200 응답 예시 필드:
`request_id`, `event_id`, `camera_id`, `role`, `media_id`, `source_sha256`, `source_bytes`, `accepted_at`, `accepted:true`, `source_state`, `media_status`, `redaction_status`.

복구 클라이언트는 로컬 outbox의 사건/카메라/request와 원본 SHA256/size를 모두 정확히 비교해야 한다. 하나라도 다르거나 receipt가 없으면 전달 미확정이며 delivered 처리·로컬 원본 삭제·맹목적 재전송을 하지 않는다. 404는 미확정이고 403은 권한/세션 오류다. 자동 재업로드 허가는 주지 않는다.

`accepted:true`는 **과거 동일 바이트의 수신 확정**이지 현재 원본 보유나 영상의 유효성·비식별화·최종판정 보장이 아니다. `source_state`는 `available`, `source_expired`, `retention_expired`, `deleted`, `unavailable` 중 하나다. TTL 이후에도 receipt metadata를 조회할 수 있지만 TTL 연장이나 원본 복원은 하지 않는다. 로컬 spool 폐기는 별도 보관/개인정보 정책을 따르며 receipt를 삭제 승인으로 해석하지 않는다. 영상 원본은 기존 private source TTL/동의철회/삭제 흐름을 따른다. receipt 메타데이터는 event/request/media 행의 삭제에 연동된다.

최초 ingest 실패 뒤 관리자가 기존 재처리 흐름으로 `clip_ingest_failed`를 복구하더라도 이 receipt를 소급 생성하지 않는다. 서버 미디어가 이후 received/ready여도 receipt는 404일 수 있다. 이는 과거 업로드 transaction 확정을 재구성하지 않는 보수적 제한이며, 클라이언트는 수동 확인이 필요한 unknown으로 유지한다. 404는 재전송 허가가 아니다.

## 영속 outbox 명령 ACK 바인딩

start/update/segments/capture-reports POST 성공 응답에는 `accepted_command`가 추가된다:
`{kind, session_id, sequence, camera_id}`. kind는 `start`, `progress`, `close`, `manifest`, `capture_report` 중 하나다. start의 sequence는 0, 나머지는 요청 sequence다. camera_id는 capture_report에서 요청 URL의 카메라이고 나머지는 null이다.

이 필드는 서버가 명령을 commit하거나 저장된 동일 body 재시도를 검증한 뒤에만 반환한다. 과거 명령 재시도 응답의 lifecycle.revision은 더 최신일 수 있지만 accepted_command는 이번에 확인한 요청을 가리킨다. 클라이언트는 event_id와 이 네 필드를 자신의 영속 명령과 정확히 비교한 뒤 ACK해야 한다. 응답이 없거나 누락·불일치면 미확정 상태를 유지하고 로컬 outbox를 삭제하면 안 된다. GET 응답이나 다른 명령의 최신 상태를 POST ACK로 대체하지 않는다. body digest 규약은 도입하지 않았고, 클라이언트는 동일 ID/순번 재시도에 저장한 원문 명령을 재사용해야 한다.

## 기존 보관만료의 worker 경계 보완

retain_until 만료는 maintenance 실행 전에도 worker claim, lease 인증/원본 조회/결과 제출, 새 enqueue와 로컬·원격 재처리를 차단한다. 정확한 만료시각도 만료다. 만료된 running 작업의 소유 worker는 원본을 더 받지 못하지만 cleanup 확인은 할 수 있으며 작업을 cancelled로 닫는다. 공개 URL/재처리 가능 표시도 만료 상태를 반영한다. 기존 보관기간이나 동의 정책을 바꾸는 변경이 아니며, 진행 중 비식별 완료 영상 열람을 일괄 금지하는 기능은 추가하지 않았다.
