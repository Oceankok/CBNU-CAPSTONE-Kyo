# 장비 작업 영역·비식별 스냅샷 계약 (#188)

## 적용 범위

장비 등록과 카메라 연결은 기존 API를 유지합니다. 이 변경은 작업 영역 DB·관리 API·추론용 정책 조회와 비식별 기준 이미지 처리를 제공합니다. 프론트 화면과 실제 YOLO 추론 루프의 영역 적용은 포함하지 않습니다.

- 백엔드: robinjh — 영역·설정 저장, 유효성·권한·버전 검사, 영역별 정책과 비식별 스냅샷 제공입니다.
- 프론트: yhw1737 — 이미지 위 다각형 지정·편집과 설정 화면 연동입니다.
- 추론·통합: Oceankok — 사람 위치별 정책 적용과 영역 진입·이탈·정책 변경 시 위반 타이머 초기화입니다.

## 관리자 API

모든 관리 API는 관리자 Bearer 인증이 필요합니다. 미인증은 401, 작업자는 403입니다.

| 메서드·경로 | 요청·응답 |
|---|---|
| `GET /api/cameras/{camera_id}/work-areas` | `camera_id`, `enabled`, `scope`, 설정 `version`, `updated_at`, `overlap_allowed: true`, `items`를 반환합니다. 미설정 버전은 0입니다. |
| `POST /api/cameras/{camera_id}/work-areas` | `name`, `equipment_id`, `points`를 전달합니다. 생성된 영역과 영역 `version: 1`을 반환합니다. 모드를 자동 활성화하지 않습니다. |
| `PATCH /api/cameras/{camera_id}/work-areas/{area_id}` | `name`, `equipment_id`, `points` 전체와 해당 영역의 `expected_version`을 전달합니다. 저장 후 영역 버전이 증가합니다. |
| `DELETE /api/cameras/{camera_id}/work-areas/{area_id}?expected_version=N` | 해당 영역을 삭제하고 최신 영역 목록과 설정 버전을 반환합니다. |
| `PUT /api/cameras/{camera_id}/work-area-config` | `{ "enabled": true, "expected_version": N }`으로 모드를 선택합니다. 여기서 버전은 영역 버전이 아닌 목록 응답의 설정 버전입니다. |
| `GET /api/cameras/{camera_id}/work-area-policy` | 현재 정책 버전·구역 기본 PPE·영역별 장비 추가 PPE와 적용 PPE를 반환합니다. |

영역 예시는 다음과 같습니다.

```json
{
  "name": "프레스 앞 작업 위치",
  "equipment_id": "관리자가 등록하고 카메라에 연결한 장비 ID",
  "points": [[0.1, 0.2], [0.6, 0.2], [0.7, 0.9], [0.1, 0.9]]
}
```

좌상단은 `[0,0]`, 우하단은 `[1,1]`입니다. 꼭짓점 3~32개의 단순 다각형만 허용합니다. 마지막 점에 첫 점을 반복하지 않습니다. 중복 점·일직선으로 퇴화한 꼭짓점·자기 교차·영면적·좌표 범위 초과는 422입니다. 시계/반시계 방향과 오목 다각형을 허용하며 카메라별 최대 32개입니다.

영역의 장비는 해당 카메라에 연결되어 있고 같은 구역의 활성 장비여야 합니다. 영역이 남은 장비의 카메라 연결을 해제하면 409 `equipment_has_work_areas`입니다. 영역을 먼저 삭제합니다. 영역과 설정의 버전 충돌은 409 `work_area_version_conflict`입니다.

## 정책과 추론 연결

처리 node는 기존 처리 권한으로 `GET /api/processing/cameras/{camera_id}/work-area-policy`를 조회합니다. 관리자용 정책 조회와 같은 응답입니다.

- 모드 OFF/미설정: `scope: camera`이며 기존 카메라 정책을 제공합니다.
- 모드 ON: `scope: work_area`이며 `zone_required_ppe`, `areas[].equipment_required_ppe`, `areas[].required_ppe`, `policy_version`을 제공합니다. 카메라 전체의 `required_ppe`는 `null`입니다.
- 영역별 미리보기는 `areas[].required_ppe`를 사용합니다. 기존 카메라 `/ppe-policy`를 영역마다 호출하면 전체 장비 합집합이므로 영역별 미리보기에 사용할 수 없습니다.
- 위치별 적용 규칙은 **구역 기본 ∪ 현재 포함된 모든 영역의 장비 추가 PPE**입니다. 영역 밖에서도 구역 기본은 유지합니다.
- 사람 위치는 원본 프레임 픽셀 박스 `[x1,y1,x2,y2]`의 하단 중앙점을 정규화하여 판정하며 경계선은 안쪽입니다. 이미지 비율을 바꾸거나 잘라낸 표시 좌표를 그대로 전달하지 않습니다.
- 영역 진입·이탈로 `area_ids`가 달라지거나 `policy_version`이 변경되면 해당 사람의 위반 지속 시간을 초기화한 뒤 새 규칙으로 다시 관측합니다. 조회 실패·불완전 설정에서도 타이머를 초기화하고 이벤트 생성을 중단합니다.
- `inference_worker.work_area_policy.resolve_person_policy`는 위치별 합집합 계산용 함수입니다. 실제 runner 연결은 추론 담당 작업으로 남아 있습니다.
- 현재 자동 사건 지원은 `helmet`입니다. 규칙에 8종 PPE를 저장할 수 있다는 사실이 모든 미착용 자동 탐지를 의미하지 않습니다. 모델 신뢰도 연결 PR #200과 별개입니다.

영역을 모두 삭제해도 모드는 자동 OFF가 되지 않습니다. `configured: false`, `work_areas_empty`로 설정 오류를 반환합니다. 비활성 장비·끊어진 연결·손상된 좌표 등도 규칙을 조용히 제외하지 않습니다. 구역·장비 PPE 변경도 `policy_version`에 반영됩니다.

기존 관리자 `/api/cameras/{camera_id}/ppe-policy`의 의미는 유지합니다. 기존 추론용 `/api/processing/cameras/{camera_id}/ppe-policy`는 모드 ON일 때 `configured: false`, `work_area_policy_required`를 반환하여 구형 runner가 전체 화면 규칙으로 계속 판정하지 않도록 합니다. **새 추론 연결이 완료되기 전 운영 카메라에서 영역 모드를 켜면 기존 runner의 이벤트 판정은 중단됩니다.**

정규화 좌표는 같은 구도의 해상도 변경에 대응합니다. 카메라 이동·회전·크롭·시야 변경은 자동 보정하지 않으며 관리자가 기준 화면을 확인하고 영역을 다시 설정해야 합니다.

## 비식별 기준 이미지

| 메서드·경로 | 용도 |
|---|---|
| `POST /api/cameras/{camera_id}/snapshots` | 최근 수신 영상으로 작업을 요청하고 202 및 상태를 반환합니다. 진행 중인 같은 카메라 요청은 재사용합니다. |
| `GET /api/cameras/{camera_id}/snapshots/{snapshot_id}` | `queued/running/ready/failed/expired`, 오류, 캡처 구간 시작 시각, 만료 시각, `image_url`을 반환합니다. 정리 후에는 404입니다. |
| `GET /api/cameras/{camera_id}/snapshots/{snapshot_id}/image` | 관리자 인증으로 완료된 JPEG만 제공합니다. `Cache-Control: private, no-store`입니다. |

프론트는 요청 후 상태를 조회하며 `ready`의 `image_url`을 인증된 요청으로 받아 표시합니다. Bearer 토큰이 필요한 URL이므로 일반 `<img src>` 대신 기존 인증 요청으로 Blob을 받아 표시하는 방식을 사용합니다. `failed`는 오류를 안내하고 새 요청을 허용합니다. 업로드 버튼이나 원본 보기 fallback은 추가하지 않습니다.

- 서버에 최근 영상이 없으면 409 `camera_frame_unavailable`입니다.
- 기존 호스트 `backend.processing.worker`가 이벤트 비식별 작업 다음에 스냅샷을 처리합니다. 별도 프로그램·외부 AI·유료 호출은 없습니다. 호스트 코드를 함께 갱신해야 합니다.
- 최근 영상 청크의 **첫 프레임**을 기존 SCRFD로 비식별화합니다. 별도 resize 없이 원래 비율을 유지합니다. `capture_start_at`은 이 청크의 시작 시각입니다.
- 원본 청크 복사본은 private 작업 폴더에만 두며, raw 읽기는 해당 처리 node의 lease로 제한합니다. 관리자·작업자에게는 원본 다운로드 API를 제공하지 않습니다.
- 요청부터 120초 뒤에는 원본·완성본 모두 접근 만료입니다. maintenance가 만료 파일을 삭제하며 파일 삭제 실패는 다음 주기에 재시도합니다. 완료·실패 뒤 원본은 worker 정리 확인 때도 제거합니다.
- 전체 활성 스냅샷은 8개, 각 원본 청크는 최대 8MiB, 결과 업로드는 최대 4MiB입니다. 정리 미확인 메타데이터를 포함한 전체 레코드 상한은 64개입니다.
- worker가 꺼져 있으면 queued로 남다가 만료됩니다. 비식별화 실패 시 원본을 대신 내보내지 않습니다. 처리 lease는 최대 90초이며 만료 뒤 늦은 결과는 거절합니다.
- worker는 원본·변환본을 지운 후 정리 확인을 보냅니다. 단절 시 journal에 따라 재시도하고 정리 미확인 상태에서는 같은 node의 새 작업 수신을 제한합니다.
- 얼굴이 탐지되지 않으면 기존 얼굴 비식별화 정책에 따라 처리됩니다. 이 기능이 모든 프레임의 얼굴 검출을 보증하지는 않습니다.

## DB·배포·검증

스키마 v17에 `camera_area_config`, `camera_work_area`, `camera_snapshot`을 추가합니다. SQLite 격리 테스트와 PostgreSQL 초기화/명시적 v16→v17 이관을 지원하며 기존 자료를 seed하거나 변환하지 않습니다. 실제 서버 DB는 이 변경을 작성하는 동안 건드리지 않습니다.

배포할 때 기존 절차로 명시적 DB 이관을 수행하고 API·호스트 worker를 같은 버전으로 맞춥니다. 초기 모드는 OFF이며 기존 카메라 정책을 유지합니다. GPU worker 자동 시작 설정은 변경하지 않습니다.

격리 테스트는 다음 명령으로 실행합니다. 테스트 안에서 임시 DB·임시 파일과 가짜 얼굴 처리 응답을 사용합니다.

```powershell
python -m unittest backend.tests.test_work_areas backend.tests.test_camera_snapshots -v
python -m unittest discover -s backend -t . -v
python -m unittest discover -s inference_worker -t . -v
```

최종 통합에서는 프론트 영역 편집 → 실제 비식별 스냅샷 → 영역 안/밖·겹침 → 정책 변경 시 타이머 초기화 → 실제 모델 후보 생성까지 별도 확인해야 합니다.
