# 카메라 관리 및 현장 영상 수신 계약

관련 이슈: #150, #155 / 송출 프로토타입: PR #157

## 카메라 관리

관리자 Bearer 인증을 사용합니다. `POST /api/cameras`는 `camera_id`, `camera_location`, `zone_name`, `process_type`, `camera_angle_type`, `status`를 받습니다. ID는 영문·숫자·하이픈·밑줄 1~100자이며 생성 후 변경하지 않습니다. 상태는 기본 `active`입니다. 구역은 등록된 구역 이름 또는 null입니다.

- `GET /api/cameras`: 기존 목록·송출/방송 node 연결 정보에 `process_type`, `camera_angle_type`을 추가합니다.
- `POST /api/cameras`: 성공 201, 중복 ID 409 `camera_already_exists`, 없는 구역 422 `zone_not_found`입니다.
- `PATCH /api/cameras/{camera_id}`: 보낸 필드만 변경합니다. 장비가 연결된 카메라의 구역 변경은 409 `camera_has_equipment_links`입니다. 구역 null은 배정 해제입니다. 상태 null은 허용하지 않습니다.
- `DELETE /api/cameras/{camera_id}`: 물리 삭제 없이 `inactive`로 전환합니다. 기존 이벤트·장비·PC 연결 이력을 유지합니다. 다시 호출해도 같은 상태입니다.
- 생성·변경·비활성화 응답은 GET 목록 항목과 같은 형태입니다.

비활성/상태 불명의 카메라에서는 새 이벤트와 영상 수신을 거절합니다. 기존 이벤트는 계속 조회·검토할 수 있습니다. 이벤트 생성 함수는 미등록 카메라에 `CameraStateError('camera_not_found', 404)`, 비활성 카메라에 `CameraStateError('camera_inactive', 409)`를 발생시킵니다. FastAPI 수신 경로에서는 같은 코드의 HTTP 응답으로 매핑합니다. 기존 이벤트 생성 함수가 새로운 HTTP 이벤트 생성 엔드포인트로 바뀐 것은 아닙니다.

## 현장 송출 인증 및 사전 설정

관리자가 기존 `POST /api/nodes`로 발급한 **현장 node 토큰**을 사용합니다. 사용자 로그인 토큰은 사용할 수 없습니다. 카메라를 등록하고 `PUT /api/cameras/{camera_id}/node`의 `source_node_id`를 송출 node에 연결해야 합니다. 활성 node와 활성 카메라만 허용합니다.

### 세션 생성

```http
POST /api/video/streams
Authorization: Bearer <현장 node 토큰>
Content-Type: application/json

{"camera_id":"CAM_001"}
```

201 응답은 `stream_id`, `camera_id`, `buffer_seconds`, `max_chunk_bytes`입니다. 같은 카메라의 현재 송출 node가 새 세션을 만들면 이전 세션은 닫힙니다. 이전 세션으로 새 조각을 보낼 수 없습니다. 세션 유휴 시간은 30초이며, 만료/재시작 시 새 세션을 생성합니다. 최대 활성 세션은 서버 전체 8개입니다. 이는 수신 상한이며 실제 GPU가 8개 카메라를 처리할 수 있다는 의미는 아닙니다.

### 조각 접수

```http
POST /api/video/streams/{stream_id}/chunks?sequence=0
Authorization: Bearer <현장 node 토큰>
Content-Type: video/mp4
X-Capture-Start: 2026-10-16T10:00:00.000Z
X-Duration-Ms: 1000

<완성된 H.264 MP4 바이너리 본문>
```

multipart가 아닌 원본 파일 본문을 전송합니다. `sequence`는 세션별로 증가하는 0~2147483647 정수입니다. 촬영 시작 시각은 timezone이 있는 실제 캡처 시각이어야 하며, 송신 시점으로 바꿔 넣지 않습니다. 서버 현재 시각보다 60초 이상 과거 또는 10초 이상 미래인 값은 거부합니다. PC 시간을 동기화하고 적체된 조각을 무제한으로 재전송하지 않습니다.

허용 범위는 독립 디코딩 가능한 H.264 MP4, 8 MiB 이하, 250~3000ms, 5~60fps, 최대 1080p·180프레임입니다. 전체 프레임 디코딩과 선언 길이의 오차 250ms 이내를 검사합니다. 기본 송출은 약 1초·15fps이며 #157의 640×360 프로토타입도 허용합니다.

202 응답은 `received`, `duplicate`, `sequence`, `chunk_id`이며 신규 조각에는 `sequence_gap`도 포함합니다. 202는 버퍼 접수 완료이며 추론 완료나 이벤트 생성을 의미하지 않습니다.

- 동일 세션·순번·영상 해시·촬영 시각·길이가 같으면 중복 접수로 202를 반환합니다.
- 같은 순번의 다른 내용은 409 `video_chunk_conflict`입니다. 순번을 건너뛴 경우 접수하고 누락 수를 세션에 누적합니다. 이미 지난 순번을 뒤늦게 채우는 요청은 409 `video_sequence_out_of_order`입니다.
- 만료된 조각의 순번도 다시 사용하지 않습니다. 재전송은 촬영 메타데이터를 포함하여 같은 내용으로 수행합니다.
- 소유 node가 다르면 403, 만료·교체 세션이면 409, 형식·길이·촬영 시각 오류는 422, MIME 오류는 415, 크기 초과는 413입니다.
- 버퍼 포화는 429 `video_buffer_full`, 동시 업로드 포화는 429 `video_upload_capacity_reached`이며 `Retry-After: 1`을 제공합니다. 송출 측은 재시도 상한·오래된 조각 폐기를 구현해야 합니다.

## AI 프로세스의 버퍼 소비

관리자가 `PUT /api/processing/workers/{node_id}`로 처리 권한을 부여한 **서버 처리 node 토큰**을 사용합니다. 일반 송출 node와 관리자 사용자 토큰에는 원본 조회 권한이 없습니다.

| API | 응답/용도 |
| --- | --- |
| `GET /api/processing/video/streams` | 활성 세션·카메라·최종 순번·누적 누락 수 |
| `GET /api/processing/video/streams/{stream_id}/chunks?after_sequence=-1` | 아직 만료되지 않은 조각의 메타데이터, 순번순 최대 40개 |
| `GET /api/processing/video/chunks/{chunk_id}` | 인증된 MP4 다운로드, `Cache-Control: private, no-store` |

AI 프로세스는 세션별 마지막 처리 순번을 관리하여 목록을 조회하고, 조각을 내려받아 디코딩·추론합니다. 동일 조각의 중복 추론 방지는 소비 프로세스가 관리합니다. 원본 파일 경로나 공개 URL은 제공하지 않습니다. Docker volume이나 DB를 네이티브 프로그램에 직접 공유하지 않습니다. 처리 권한은 기존 비식별화 worker와 같은 신뢰 수준이며, 모든 카메라 버퍼를 읽을 수 있으므로 현장 송출 PC에 부여하지 않습니다.

실시간 PPE 추론의 우선 신호는 기존 `/api/processing/inference-load`를 사용합니다. 이벤트 발생 시 AI 프로세스가 전후 클립을 생성하여 기존 `/api/processing/events/{event_id}/clip-requests`와 `/clip`으로 전달합니다. 영상 수신 API가 자체적으로 추론·이벤트·클립 생성을 실행하지는 않습니다.

## 임시 원본과 한계

조각은 `.private_media/video_ingest`에만 보관합니다. 접수 후 30초가 지나면 조회·다운로드에서 제외하고 수신/조회 시 정리 또는 기존 maintenance에서 실제 파일을 삭제합니다. 삭제 실패는 DB 참조를 유지해 재시도하고 용량 계산에서 제외하지 않습니다. 세션·카메라 연결 변경 또는 비활성화 후에는 원본 접근을 거부합니다.

카메라당 최대 40개·64 MiB, 서버 전체 256 MiB 버퍼 상한이 있습니다. 별도로 업로드/검사 중 임시 파일은 API 프로세스당 최대 8개·각 8 MiB입니다. 현재 단일 API 프로세스 구성을 기준으로 하며 수평 확장은 이번 범위가 아닙니다. 만료 세션의 빈 메타데이터는 24시간 뒤 정리합니다.

AI 프로세스가 원본을 내려받아 만든 별도 사본·프레임 버퍼·인코딩 파일은 해당 프로세스가 상한·만료·종료 시 삭제를 관리해야 합니다. 이벤트 전후 구간은 원본이 만료되기 전에 확보해야 합니다. DB에 장기 영상 큐를 쌓거나 원본을 검토 화면에 직접 제공하지 않습니다. 서버 중단 중에는 maintenance가 실행되지 않으므로 재시작 후 정리를 수행합니다.

## #157과 연결하기 전 필요한 송출 보완

엔드포인트와 본문 형식은 #157과 같습니다. 실제 수신부는 임시 수신 서버와 달리 인증·메타데이터·크기·형식을 검증합니다.

1. `VIDEO_NODE_TOKEN`에 실제 node 토큰을 설정합니다.
2. 캡처 시각을 인코더/조각별로 기록하고 `X-Capture-Start`를 전송합니다. 현재 #157에는 이 헤더가 없어 그대로 연결하면 422입니다.
3. 마지막 작성 중 파일 제외, 재시도/순서/세션 재생성, 송출 완료 파일 삭제와 디스크/대기열 상한을 구현합니다.
4. 서버 AI 프로세스는 위 버퍼 조회·다운로드 API를 연결합니다. 다중 카메라·실제 지연·전체 이벤트 흐름은 #139 통합 검증에서 확인합니다.

## DB 반영 및 검증

SQLite·PostgreSQL 스키마 v10에 `video_stream`, `video_chunk`를 추가합니다. v9→v10 이관은 기존 계정·이벤트·미디어를 유지합니다. 실제 개발/배포 DB에는 이 작업에서 이관을 실행하지 않습니다. 배포 절차상 스키마 업그레이드는 백업 후 별도 단계로 수행합니다.

자동 테스트는 임시 DB·인물 없는 H.264 샘플로 카메라 등록/수정/비활성화, 권한, 조각 접수·중복·순번·세션 교체·용량 제한·원본 접근·만료 삭제와 이관을 검증합니다. 실제 현장 카메라·GPU 추론·음성 출력·외부 영상 전송과는 구분합니다.

2026년 10월 16일 관련 테스트 13개와 SQLite·PostgreSQL 전체 회귀를 확인했습니다. 전체 회귀는 각 101개 중 97개 통과·DB별 전용 4개 제외이며 테스트용 PostgreSQL 컨테이너와 volume을 정리했습니다. #157 송출 프로그램 및 실제 AI 프로세스와의 연결 검증은 메타데이터 보완 후 별도로 수행합니다.

## 원격 Windows 실행 안내

서로 다른 네트워크에서 실제 카메라를 연결하는 절차와 검증 결과는 [원격 카메라 연결 안내](20261026_Remote_Camera_Connection.md)에 정리했습니다. 위 API 계약과 함께 사용합니다.
