# 미디어 재처리 및 검토 상태 연동

## 변경 범위

운영 검토 화면은 자동 수신된 비식별 자료를 사용합니다. 기존 개발용 업로드 API는 유지하며, 운영 화면에 업로드 버튼을 추가하지 않습니다. 이번 변경에서는 프론트 파일을 수정하지 않습니다.

클립 요청 상태를 저장하는 DB/API를 추가합니다. **이 API는 상태 기록용이며 현장 PC에 명령을 전송하거나 영상을 자르는 기능은 아닙니다.** 현장 클립 자동 요청·수신 파이프라인은 별도로 연결해야 합니다. 동의서 업로드는 이번 범위에 포함하지 않습니다.

## DB 적용

서버를 종료하고 저장소 루트에서 다음 명령으로 기존 DB를 버전 4로 갱신합니다. 기존 이벤트·검토 데이터를 삭제하지 않습니다.

```powershell
python backend/db/init_db.py
```

`event_media`에 `source_path`, `source_expires_at`을 추가하고, `event_clip_request`를 생성합니다. `quarterly_summary`에는 `unreviewable_count`를 추가합니다. 기존 상태 CHECK 값은 유지하고, 실패 원인은 `error_code`와 응답의 `failure_category`로 구분합니다. 이벤트·검토 결과는 기존 TEXT 필드에 `unreviewable`을 저장합니다.

## 비공개 원본 및 재처리

- 새로 수신하거나 업로드한 원본은 `.private_media/originals/`에 보관합니다. API는 원본 경로·다운로드 URL을 제공하지 않습니다.
- 기본 보관 기간은 24시간입니다. `PPE_MEDIA_SOURCE_TTL_SECONDS`를 양의 정수 초로 설정하여 변경할 수 있습니다. 이 값은 새 원본에 적용합니다.
- 삭제 결정 시 처리 결과와 원본을 함께 삭제합니다. 삭제 실패는 기존 재시도 흐름으로 처리합니다.
- maintenance가 실행되면 만료된 원본을 삭제합니다. 만료된 원본은 정리 실행 전에도 재처리 대상에서 제외합니다. 정상 비식별 결과는 원본 만료만으로 삭제하지 않습니다.
- 기존 DB의 처리 결과를 원본으로 간주하지 않습니다. 과거 원본이 이미 삭제된 자료는 새 코드만으로 재처리할 수 없습니다. 기존 `legacy_unverified` 자료는 안전한 저장 경로에 실제 파일이 있으면 비공개 원본으로 이동한 뒤 최초 비식별화를 수행합니다.

기존 `POST /api/media/{media_id}/redact`를 사용합니다. 원본이 있는 `ready` 자료도 재처리할 수 있습니다. 요청 시 DB 잠금 안에서 조건을 재검사하여 중복 처리와 삭제 중 재처리를 차단합니다.

미디어 응답에 아래 필드를 제공합니다.

| 필드 | 의미 |
|---|---|
| `can_reprocess` | 현재 재처리 가능 여부입니다. DB에 저장하지 않습니다. |
| `reprocess_unavailable_reason` | 가능하면 `null`, 불가능하면 사유 코드입니다. |
| `source_expires_at` | 원본 재처리 기한이며 Unix 초입니다. |
| `failure_category` | 실패 종류입니다. 오류가 없으면 `null`입니다. |

재처리 불가 사유는 `deletion_requested`, `processing_in_progress`, `source_expired`, `source_file_missing`, `source_invalid`, `unsafe_source_path`, `unsafe_storage_path`입니다. 원본 손상으로 판정된 자료는 같은 파일을 반복 처리하지 않고 클립 재요청 대상으로 안내합니다.

실패 종류는 `source_unavailable`, `source_invalid`, `redaction_failed`, `cleanup_failed`입니다. 상세 원인은 기존 `error_code`로 제공합니다.

## 검토 가능 상태

이벤트 조회 및 `GET /api/events/{event_id}/media`에 `review_availability`, `clip_requests`를 추가합니다.

```json
{
  "review_availability": {
    "state": "awaiting_clip",
    "can_review": false,
    "can_mark_unreviewable": false,
    "missing_media_count": 0
  }
}
```

`state`는 `available`, `awaiting_clip`, `processing`, `retryable_failure`, `no_usable_media` 중 하나입니다. 일부 카메라 자료가 실패했더라도 정상 비식별 자료가 있으면 검토를 허용합니다. `missing_media_count`는 URL이 없는 미디어 행 수이며, 등록되지 않은 카메라 수를 의미하지 않습니다.

- 실제 존재하는 비식별 완료 자료가 없으면 `confirmed`·`false_positive` 저장을 `409 usable_redacted_media_required`로 차단합니다.
- `hold`는 자료 대기 중에도 허용합니다.
- 클립 수신 대기·재요청이 필요한 실패·진행 중 처리·재처리 가능한 자료가 있으면 `unreviewable` 저장을 `409 media_recovery_pending`으로 차단합니다.
- 복구할 수 없는 경우에는 기존 검토 API에 아래 값을 전달합니다. 관리자가 복구 불가 사실과 사유를 확인하고 기록합니다.

```json
{
  "review_result": "unreviewable",
  "review_reason_code": "source_missing",
  "review_comment": "현장 PC에서도 원본 복구가 불가능함을 확인했습니다.",
  "second_review_needed": false
}
```

사유는 `source_missing`, `source_corrupt`, `clip_unavailable` 중 하나로 제한하며 설명을 필수로 받습니다. 검토자 ID는 인증 정보에서 결정합니다. 자료를 나중에 복구한 경우에는 `unreviewable` 이벤트를 재검토할 수 있으며 기존 결정은 이력에 남습니다. 검토 불가 자료의 retain 결정은 차단하고 delete 결정은 허용합니다.

통계 응답의 `summary.unreviewable_count`로 별도 집계합니다. 기존 저장된 분기 통계는 새로 생성하기 전까지 해당 값이 0입니다. 확정 위반 기반 교육 추천에는 포함하지 않습니다.

## 클립 요청 상태 기록 API

관리자 인증이 필요합니다. 현장 명령 전송은 이 API에 포함하지 않습니다.

- `POST /api/events/{event_id}/clip-requests`: `{ "camera_id": "CAM_001" }`로 `pending` 기록을 생성합니다. 같은 이벤트·카메라의 진행 중 요청은 재사용합니다.
- `PUT /api/events/{event_id}/clip-requests/{request_id}`: `received`, `failed`, `unavailable` 결과를 기록합니다.
- `received`는 이벤트·카메라에 해당하는 기존 미디어의 `media_id`가 필요하며 그 미디어에 요청을 연결합니다.
- `failed`는 일시 실패이며 재요청이 필요하므로 검토 불가 확정을 차단합니다. 새 요청을 등록하거나, 복구 불가를 확인한 뒤 `unavailable`로 변경합니다.
- `failed`·`unavailable`에는 `error_code`가 필수입니다. 카메라별 최신 요청 상태를 기준으로 검토 가능 여부를 계산합니다.

## 프론트 요청 사항

운영 화면에서 업로드를 제외하고, 관리자 재처리 버튼에 `can_reprocess`와 비활성 사유를 연결합니다. 실패 종류별로 재처리·클립 재요청·복구 불가 안내를 구분합니다. 검토 가능 상태에 맞춰 확정·오탐·검토 불가 조작을 제한합니다. 기존 데이터의 원본을 복원할 수 있다는 안내는 표시하지 않습니다. 화면 변경은 프론트 담당 PR로 진행합니다.

## 검증 방법

아래 테스트는 격리 DB·임시 파일을 사용하며 실제 얼굴 추론을 실행하지 않습니다. 실행 승인을 받은 후 수행합니다.

```powershell
python -m unittest backend.tests.test_media_recovery backend.tests.test_auth -v
python -m unittest discover -s backend -t . -v
```

새 테스트에서는 모델 오류 후 원본 보존·재시도, 원본 없음·만료·처리 중 차단, 손상 분류, 원본/결과 동시 삭제, 원본 만료 정리, 검토 상태·클립 대기 차단, 복구 후 재검토, 통계 분리 및 마이그레이션을 확인합니다. 기존 인증 테스트의 검토자·쿠키 검증 의도는 유지하고 비식별 완료 준비 데이터를 추가합니다.

실제 GPU 검증에서는 개발용 업로드로 새 자료를 등록하고, 원본 보관 중 재처리, 결과 게시, 삭제 후 원본과 결과 제거를 확인합니다. 운영 PC에서 maintenance를 주기적으로 실행해야 원본 만료 및 삭제 재시도가 동작합니다.
