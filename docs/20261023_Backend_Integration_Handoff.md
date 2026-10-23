# 백엔드 통합 연결 및 검증 안내

이 문서는 동료 프로그램이 연결할 API와 재실행 가능한 격리 검증을 정리합니다. 프론트 화면과 YOLO·Tracking·클립 생성 프로그램은 수정하지 않았습니다. 실제 서버 주소·현장 장치·상용 교육 AI 공급업체는 자동으로 정하지 않습니다.

시연은 최신 PostgreSQL에 새 DB를 초기화하는 방향으로 진행합니다. 기존 개발용 SQLite·더미 데이터 이관은 필요하지 않습니다. 첫 배포는 [서버 배포 안내](20261013_Server_Release_Setup.md)를 따르며, 기존 PostgreSQL 데이터를 유지하기로 선택한 경우에만 [선택적 이관·복구 안내](20261023_Release_Upgrade_Runbook.md)를 사용합니다. 독립 송신 수정 #173도 함께 반영합니다.

## 적용 순서와 담당

선행 PR은 #167 → #168 → #170 → #171이며, #172를 마지막에 반영합니다. #174의 이관·자동 복구는 이 기능들의 필수 선행 조건이 아닙니다. 실제 배포 DB는 아직 변경하지 않았습니다. 최신 API 이미지로 새 PostgreSQL DB를 초기화하면 스키마 v14가 생성됩니다. 최신 API/web 이미지와 호스트 worker를 함께 사용합니다. 기존 배포 폴더·DB에 첫 배포용 초기화를 다시 실행하지 않으며, 새 시연 DB는 별도 배포 폴더·Compose 프로젝트로 준비합니다. 기존 서버를 대체할 때의 포트·worker 연결 전환은 실제 배포 시 확인합니다.

| 담당 | 연결할 부분 | 백엔드 준비 내용 |
| --- | --- | --- |
| Oceankok | 현장 영상 송신·서버 PPE 추론·시간대별 클립 확보 | 원본 영상 수신, 처리 node용 영상·PPE 정책 조회, 이벤트 접수, 클립 요청·접수·실패 보고, worker 작업 프로토콜 |
| yhw1737 | 관리자 동의서·교육 자료·카메라 그룹·수동 병합 화면 | #167·#168·#170·#171의 API와 계약 문서 |
| robinjh | 백엔드·DB·worker·방송 API·배포·회귀 | 이 문서의 HTTP 통합 검증 및 Docker 검증 |

`processing` API는 등록 후 처리 권한을 활성화한 서버 node 토큰을 사용합니다. 관리자 Bearer 토큰이나 일반 현장 node 토큰을 대신 사용하지 않습니다. 관리자 API는 관리자 토큰, 송출·방송 API는 해당 현장 node 토큰을 사용합니다. 실제 토큰을 문서나 저장소에 기록하지 않습니다.

## AI·통합 프로그램 연결 순서

1. 관리자가 현장·처리 node와 카메라를 등록하고 입력/출력 PC를 연결합니다. `GET /api/cameras`에서 현재 연결을 확인합니다.
2. 현장 프로그램이 `POST /api/video/streams`로 카메라 스트림을 열고 `POST /api/video/streams/{stream_id}/chunks?sequence=N`으로 1초 MP4 바이너리를 보냅니다. `Content-Type: video/mp4`, `X-Capture-Start`(시간대 포함), `X-Duration-Ms`를 전달합니다. 세부 제한은 #155 계약을 따릅니다.
3. 처리 프로그램이 기존 `GET /api/processing/video/streams`·`.../{stream_id}/chunks`·`/api/processing/video/chunks/{chunk_id}`로 영상을 조회합니다. 원본 청크는 검토 화면용 URL이 아닙니다.
4. **`GET /api/processing/cameras/{camera_id}/ppe-policy`**로 기존 관리자 정책과 같은 적용 PPE를 조회합니다. `configured=false`와 `configuration_issues`를 정상 정책으로 간주하지 않습니다. 장비가 없더라도 관리자 API에서 빈 `equipment_ids`를 명시적으로 저장하면 구역 규칙만 적용하도록 구성할 수 있습니다. 실제 탐지 대상·위반 지속 시간 판정은 AI 프로그램에서 구현합니다.
5. 위반이 성립하면 아래 이벤트 접수 API를 호출합니다. 서버가 이벤트와 트리거·그룹 참고 클립 요청을 한 트랜잭션에서 생성하고 기존 방송 큐를 호출합니다. 같은 장면을 다른 카메라가 별도 사건으로 보내면 자동 병합하지 않습니다.
6. `GET /api/processing/events/{event_id}/clip-requests`의 카메라·송출 PC·시간을 사용해 클립을 확보하고 `POST /api/processing/events/{event_id}/clip?request_id=...`에 MP4 바이너리를 보냅니다. 그룹 카메라의 참고 영상도 포함합니다. 구간 확보가 실패하면 처리 node용 `PUT .../clip-requests/{request_id}`로 `failed` 또는 `unavailable`과 사유를 보고합니다.
7. 호스트 worker가 기존 claim·source·lease·result·cleanup API로 비식별화합니다. 서버 PC의 PPE 추론 프로그램은 기존 `PUT /api/processing/inference-load` 신호로 비식별화 작업 시작을 미룰 수 있습니다. 실행 중인 작업을 강제로 선점하는 기능은 아닙니다.
8. 현장 방송 프로그램은 기존 heartbeat·commands/claim·result API로 경고를 출력하고 완료를 보고합니다. 서버는 스피커를 직접 출력하지 않습니다.
9. 검토 화면은 `GET /api/events/{event_id}`와 비식별화 완료 URL만 사용합니다. 필요 시 수동 병합 후 대표 이벤트를 검토·보관·삭제합니다.

## 추론 이벤트 접수

```http
POST /api/processing/events
Authorization: Bearer <서버 처리 node 토큰>
Content-Type: application/json

{
  "event_id": "EVT_0123456789abcdef0123456789abcdef",
  "camera_id": "CAM_A",
  "tracking_id": "camera-local-42",
  "ppe_type": "helmet",
  "timestamp_start": "2026-10-23T10:00:00+09:00",
  "timestamp_end": "2026-10-23T10:00:03+09:00",
  "confidence": 0.94,
  "frame_sample_count": 45,
  "model_version": "team-best-v1"
}
```

`event_id`는 AI 프로그램에서 `"EVT_" + uuid.uuid4().hex`로 한 번 생성하고 동일 사건 재전송 시 그대로 사용합니다. 처음 접수하면 201, 같은 ID·같은 내용 재전송은 200과 `created=false`입니다. 동일 ID의 내용이 다르면 409입니다. 네트워크 오류가 날 때 새 UUID로 다시 보내면 다른 이벤트가 되므로 같은 ID로 재시도합니다. 이 ID는 카메라 간 동일 인물 판정을 의미하지 않습니다.

응답은 `event_id`, `created`, `clip_requests`, `broadcast`, `merged_into_event_id`를 제공합니다. 클립 요청에는 원본 카메라 `role=clip`, 그룹 참고 카메라 `role=reference`가 들어 있습니다. 방송은 기존 이벤트별 중복 방지·cooldown을 적용하며, 이벤트 저장 이후 방송 큐에서 오류가 나더라도 같은 ID로 재시도하면 이벤트·클립 요청이 중복되지 않습니다. 이미 검토·병합한 이벤트의 재전송으로 방송을 새로 시작하지 않습니다.

등록되지 않은 카메라는 404, 비활성 카메라는 409입니다. 타임스탬프는 시간대가 있어야 하고 종료가 시작 이상이어야 하며 관측 구간은 최대 1시간입니다. 서버에는 UTC로 정규화해 저장합니다. 신뢰도는 0~1의 유한수, 샘플 수는 1~100000, PPE는 기존 8종 코드입니다. 원본 파일 경로·사용자 역할·검토 결과를 이 본문으로 받지 않습니다.

이 경로를 사용할 때 기존 `create_no_helmet_candidate_event()` 또는 직접 DB 저장을 같은 탐지에 추가 호출하지 않습니다. 내부 함수는 기존 로컬 호출 호환용으로 유지합니다. 이 API가 YOLO 추론·규칙 판단·클립 생성을 대신 수행하지 않습니다.

## 프론트 연결 문서

- 동의서: `docs/20261023_Consent_Records_API.md`
- 교육 자료 작성·승인·게시: `docs/20261023_Education_Materials_API.md`
- 카메라 그룹·참고 클립: `docs/20261023_Camera_Group_Reference_API.md`
- 수동 병합·대표 이동·409 조건: `docs/20261023_Manual_Event_Merge_API.md`

동의서 파일은 일반 공개 미디어와 분리된 관리자 인증 API로 다운로드합니다. 동의 철회는 연결된 보관 미디어를 삭제 대상으로 전환하며 최소 기록은 유지합니다. 교육 자료는 관리자 게시 전 작업자에게 노출하지 않습니다. 기존 작업자 `material`은 문자열 계약을 유지합니다. 게시 취소·자동 번역·상용 AI 초안 생성은 이번 기본 연동에 포함하지 않습니다.

## 바로 실행할 격리 검증

프로젝트 루트에서 설치된 시스템 Python을 사용합니다. 실제 개발 DB·모델·스피커·외부 VLM을 사용하지 않습니다.

```powershell
$env:PYTHONUTF8 = "1"
$env:PPE_TEST_POSTGRES_URL = ""
python -m unittest backend.tests.test_processing_events backend.tests.test_backend_integration -v
```

같은 HTTP 시나리오는 일반 backend·PostgreSQL CI에도 포함됩니다. 실제 얼굴 정확도·PPE 추론·스피커 출력은 여기서 검증했다고 간주하지 않습니다.

Docker와 ffprobe가 실행 가능한 환경에서 다음 명령으로 현재 소스의 API/web 이미지를 빌드하고 임시 PostgreSQL에서 같은 흐름을 실제 HTTP·웹 프록시로 검증합니다. 송신기 `requests`는 `video_sender/requirements.txt`로 설치합니다. Docker가 꺼져 있으면 자동으로 권한 창이나 Docker Desktop을 열지 않습니다.

```powershell
$env:PYTHONUTF8 = "1"
python deploy/tests/validate_backend_integration.py
```

이 명령은 주소나 기존 계정·볼륨을 입력받지 않습니다. 무작위 이름의 전용 프로젝트·계정·포트·볼륨을 사용하고 종료 시 해당 테스트 컨테이너·볼륨·빌드 이미지만 제거합니다. `kyo-server`를 시작·정지하거나 실제 서버 데이터를 이관하지 않습니다. 테스트 서비스에만 합성 클립·PDF를 생성하며, 결과는 `.private_media/backend-integration-results.json`에 저장합니다. 비밀번호·토큰은 결과에 포함하지 않습니다.

Docker 검증에서는 PostgreSQL v10 이관·실제 dump 복원 리허설, 실제 송신 루프의 ffprobe·HTTP 전송, 웹 프록시·영상 수신·방송 명령 왕복·원본 접근 제한·클립/worker 프로토콜·병합·검토·동의 보관/철회·실제 파일 삭제·교육 게시와 컨테이너 재생성 후 데이터 유지를 확인합니다. CI의 `integration` 작업에서도 실행하고 결과 JSON을 artifact로 남깁니다. 실제 얼굴 검출 대신 인물이 없는 영상을 가짜 worker 결과로 반환합니다.

## 동료 연결 후 남는 실제 검증

- 실제 카메라 또는 재생 영상 → YOLO → 이벤트 접수 → 원본·참고 클립 확보
- 실제 GPU worker의 얼굴 비식별화·시연 영상 정확도와 처리 속도
- 실제 현장 PC 스피커·선택 voice의 음성 출력
- 프론트 화면에서 동의서·교육·그룹·수동 병합 조작과 역할별 접근
- 별도 PC·모바일의 네트워크 접속 및 최종 시연 시간 동기화

위 항목은 대응 프로그램·화면이 연결된 후 확인하며, 이 백엔드 통합 검증의 PASS에 포함하지 않습니다.
