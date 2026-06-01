# Backend API

이 폴더는 PPE 분석 시스템의 FastAPI 서버 코드를 포함함.

현재 API는 AI가 저장한 PPE 미착용 후보 이벤트를 조회하고, 관리자의 검토 결과를 반영하며, 확정 위반 데이터를 기반으로 분기별 통계와 교육 추천을 생성하는 기능을 제공함. 또한 경고 방송 설정 관리와 후보 이벤트 미디어 파일 제공 기능을 포함함.

---

## 파일 구성

| 파일            | 설명                               |
| ------------- | -------------------------------- |
| `main.py`     | FastAPI 애플리케이션 및 API endpoint 정의 |
| `__init__.py` | `backend.api` 패키지 인식용 파일         |

---

## 실행 전 준비

API 실행 전 SQLite DB를 초기화함.

```bash
python backend/db/init_db.py
```

Windows 환경에서 `python` 명령어가 동작하지 않을 경우:

```bash
py backend/db/init_db.py
```

추가 테스트 이벤트가 필요한 경우 아래 스크립트를 실행함.

```bash
python backend/db/seed_events.py
```

---

## API 서버 실행

프로젝트 루트에서 아래 명령어를 실행함.

```bash
python -m uvicorn backend.api.main:app --reload
```

Windows 환경에서 `python` 명령어가 동작하지 않을 경우:

```bash
py -m uvicorn backend.api.main:app --reload
```

정상 실행 시 아래 주소에서 서버 상태를 확인할 수 있음.

```text
http://127.0.0.1:8000
```

Swagger 문서는 아래 주소에서 확인 가능함.

```text
http://127.0.0.1:8000/docs
```

---

## 제공 API

| Method | Endpoint                        | 설명                           |
| ------ | ------------------------------- | ---------------------------- |
| GET    | `/`                             | API 서버 상태 확인                 |
| GET    | `/api/events`                   | 후보 이벤트 전체 목록 조회              |
| GET    | `/api/events/{event_id}`        | 후보 이벤트 단건 및 검토 결과 조회         |
| POST   | `/api/events/{event_id}/review` | 후보 이벤트 최초 검토 결과 저장           |
| PUT    | `/api/events/{event_id}/review` | 보류 또는 2차 검토 대상 이벤트 재검토 결과 갱신 |
| GET    | `/api/stats`                    | 생성된 분기별 통계 조회                |
| POST   | `/api/stats/generate`           | 후보 이벤트 및 검토 결과 기반 분기별 통계 생성  |
| GET    | `/api/recommendations`          | 생성된 교육 추천 결과 조회              |
| POST   | `/api/recommendations/generate` | 확정 위반 데이터 기반 교육 추천 생성        |
| GET    | `/api/broadcast/settings`       | 경고 방송 설정 조회                  |
| PUT    | `/api/broadcast/settings`       | 경고 방송 설정 저장                  |

추가로 후보 이벤트 썸네일 및 영상 참조 파일은 `/storage` 경로를 통해 정적 파일로 제공함.

---

# Events API

## 후보 이벤트 전체 목록 조회

### `GET /api/events`

DB에 저장된 후보 이벤트 목록을 최신 발생 시각 순으로 조회함.

현재 API는 서버 측 필터 query parameter를 제공하지 않으며, 프론트엔드 목록 화면에서 전체 이벤트를 조회한 뒤 표시 단계에서 필터링함.

### 요청 예시

```http
GET /api/events
```

### 응답 예시

```json
{
  "total": 2,
  "items": [
    {
      "event_id": "EVT_0001",
      "camera_id": "CAM_001",
      "zone_name": "프레스 구역",
      "process_type": "유압 성형",
      "tracking_id": "TRK_001",
      "ppe_type": "helmet",
      "timestamp_start": "2026-04-28 10:15:00",
      "timestamp_end": "2026-04-28 10:15:03",
      "duration_sec": 3,
      "frame_sample_count": 72,
      "thumbnail_path": "storage/candidate_events/thumbnails/EVT_0001.jpg",
      "video_clip_path": "storage/candidate_events/clips/EVT_0001.mp4",
      "ai_confidence": 0.87,
      "person_detected": 1,
      "ppe_detected": 0,
      "model_version": "helmet_yolov8n",
      "event_status": "pending"
    }
  ]
}
```

---

## 후보 이벤트 단건 조회

### `GET /api/events/{event_id}`

특정 후보 이벤트와 해당 이벤트의 관리자 검토 결과를 함께 조회함.

### 요청 예시

```http
GET /api/events/EVT_0001
```

### 검토 전 응답 예시

```json
{
  "event": {
    "event_id": "EVT_0001",
    "camera_id": "CAM_001",
    "zone_name": "프레스 구역",
    "process_type": "유압 성형",
    "ppe_type": "helmet",
    "event_status": "pending"
  },
  "review": null
}
```

### 검토 후 응답 예시

```json
{
  "event": {
    "event_id": "EVT_0001",
    "camera_id": "CAM_001",
    "zone_name": "프레스 구역",
    "process_type": "유압 성형",
    "ppe_type": "helmet",
    "event_status": "confirmed"
  },
  "review": {
    "review_id": "RV_0001",
    "event_id": "EVT_0001",
    "reviewer_id": "admin01",
    "review_result": "confirmed",
    "review_reason_code": "confirmed_no_helmet",
    "review_comment": "실제 안전모 미착용",
    "confirmed_violation": 1,
    "second_review_needed": 0
  }
}
```

존재하지 않는 이벤트를 조회하면 아래 오류를 반환함.

```text
404 Event not found
```

---

# Review API

관리자 검토 결과는 아래 세 가지 값 중 하나로 저장함.

| `review_result`  | 의미       | 처리 방식                              |
| ---------------- | -------- | ---------------------------------- |
| `confirmed`      | 실제 위반 확인 | 이벤트 상태를 `confirmed`로 변경하고 검토 결과 저장 |
| `hold`           | 판단 보류    | 이벤트 상태를 `hold`로 변경하고 검토 결과 저장      |
| `false_positive` | 오탐       | 비식별 오탐 집계에 반영한 뒤 상세 후보 이벤트 삭제      |

`false_positive`로 판단된 이벤트는 상세 이미지·이벤트 검토 데이터로 장기 보관하지 않고, 분기·구역·PPE 유형 기준의 집계값만 보존함.

---

## 최초 검토 결과 저장

### `POST /api/events/{event_id}/review`

아직 검토 결과가 저장되지 않은 후보 이벤트에 대해 최초 검토를 제출함.

### 요청 예시

```http
POST /api/events/EVT_0001/review
```

```json
{
  "reviewer_id": "admin01",
  "review_result": "confirmed",
  "review_reason_code": "confirmed_no_helmet",
  "review_comment": "실제 안전모 미착용",
  "second_review_needed": false
}
```

### 정상 응답 예시

```json
{
  "status": "ok",
  "message": "Review saved successfully",
  "event": {
    "event_id": "EVT_0001",
    "event_status": "confirmed"
  },
  "review": {
    "review_id": "RV_0001",
    "event_id": "EVT_0001",
    "reviewer_id": "admin01",
    "review_result": "confirmed",
    "confirmed_violation": 1,
    "second_review_needed": 0
  }
}
```

같은 이벤트에 대해 최초 검토를 다시 제출하면 아래 오류를 반환함.

```text
409 Review already exists
```

---

## 재검토 결과 갱신

### `PUT /api/events/{event_id}/review`

기존 검토 결과가 있는 이벤트 중 아래 조건을 만족하는 경우에만 재검토 결과를 제출할 수 있음.

| 기존 상태                                                       | 재검토 가능 여부 |
| ----------------------------------------------------------- | --------- |
| `event_status = hold`                                       | 가능        |
| 기존 검토 결과의 `second_review_needed = true`                     | 가능        |
| `event_status = confirmed`이고 `second_review_needed = false` | 불가능       |
| 기존 검토 결과가 없는 이벤트                                            | 불가능       |

### 요청 예시

```http
PUT /api/events/EVT_0001/review
```

```json
{
  "reviewer_id": "admin02",
  "review_result": "confirmed",
  "review_reason_code": "confirmed_no_helmet",
  "review_comment": "재검토 결과 실제 위반 확인",
  "second_review_needed": false
}
```

### 정상 응답 예시

```json
{
  "status": "ok",
  "message": "Review updated successfully",
  "event": {
    "event_id": "EVT_0001",
    "event_status": "confirmed"
  },
  "review": {
    "review_id": "RV_0001",
    "event_id": "EVT_0001",
    "reviewer_id": "admin02",
    "review_result": "confirmed",
    "review_reason_code": "confirmed_no_helmet",
    "review_comment": "재검토 결과 실제 위반 확인",
    "confirmed_violation": 1,
    "second_review_needed": 0
  }
}
```

### 오탐 재검토 응답 예시

재검토 결과가 `false_positive`인 경우에는 비식별 오탐 집계에 반영한 뒤 후보 이벤트를 삭제함.

```json
{
  "status": "ok",
  "message": "False positive event deleted after re-review",
  "event_id": "EVT_0001",
  "review_result": "false_positive"
}
```

### 재검토 오류 응답

| 상황                         | 응답                                                                  |
| -------------------------- | ------------------------------------------------------------------- |
| 이벤트가 존재하지 않음               | `404 Event not found`                                               |
| 기존 검토 결과가 없음               | `409 Review does not exist`                                         |
| 재검토 가능 조건을 만족하지 않음         | `409 Event is not eligible for re-review`                           |
| 허용되지 않은 `review_result` 전달 | `400 review_result must be one of: confirmed, false_positive, hold` |

---

# Stats API

## 분기별 통계 조회

### `GET /api/stats`

이미 생성된 분기별 통계 데이터를 조회함.

### Query Parameter

| 이름        | 기본값       | 설명                      |
| --------- | --------- | ----------------------- |
| `quarter` | `2026-Q2` | 조회할 분기. `YYYY-QN` 형식 사용 |

### 요청 예시

```http
GET /api/stats?quarter=2026-Q2
```

### 응답 데이터

| 항목            | 설명                             |
| ------------- | ------------------------------ |
| `summary`     | 전체 후보 이벤트, 확정 위반, 오탐 집계, 보류 건수 |
| `by_ppe_type` | PPE 유형별 확정 위반 건수 및 우선순위 점수     |
| `by_zone`     | 구역별 확정 위반 건수 및 우선순위 점수         |
| `trend`       | 분기별 안전모·안전조끼 확정 위반 추이          |

### 응답 예시

```json
{
  "quarter": "2026-Q2",
  "summary": {
    "quarter": "2026-Q2",
    "candidate_count": 15,
    "confirmed_count": 4,
    "false_positive_count": 1,
    "hold_count": 2
  },
  "by_ppe_type": [
    {
      "ppe_type": "helmet",
      "confirmed_count": 3,
      "priority_score": 2.0
    }
  ],
  "by_zone": [
    {
      "zone_name": "프레스 구역",
      "confirmed_count": 3,
      "priority_score": 2.0
    }
  ],
  "trend": [
    {
      "quarter": "2026-Q2",
      "helmet": 3,
      "vest": 1
    }
  ]
}
```

생성된 통계 데이터가 없는 경우 아래 오류를 반환함.

```text
404 Quarterly stats not found
```

---

## 분기별 통계 생성

### `POST /api/stats/generate`

후보 이벤트 및 관리자 검토 결과를 기반으로 지정 분기의 통계 데이터를 생성함.

### 요청 예시

```http
POST /api/stats/generate?quarter=2026-Q2
```

### 생성 기준

| 데이터         | 반영 기준                               |
| ----------- | ----------------------------------- |
| 후보 이벤트 수    | 지정 분기에 발생한 `candidate_event` 건수     |
| 확정 위반 수     | `event_status = confirmed`인 이벤트 수   |
| 보류 수        | `event_status = hold`인 이벤트 수        |
| 오탐 수        | `false_positive_aggregate`의 비식별 집계값 |
| PPE·구역 우선순위 | 확정 위반 건수, 반복 발생 주차, 집중도 기준 계산       |

생성된 통계 데이터는 동일 응답 구조로 반환됨.

---

# Recommendations API

## 교육 추천 결과 조회

### `GET /api/recommendations`

이미 생성된 분기별 교육 추천 결과를 조회함.

### 요청 예시

```http
GET /api/recommendations?quarter=2026-Q2
```

### 응답 예시

```json
{
  "quarter": "2026-Q2",
  "generated_at": "2026-05-28 18:00:00",
  "items": [
    {
      "recommendation_id": "EDU_2026Q2_01",
      "recommendation_rank": 1,
      "ppe_type": "helmet",
      "zone_name": "프레스 구역",
      "education_topic": "프레스 구역 안전모 착용 기준 및 착용 전 점검 절차 교육",
      "priority_score": 2.0,
      "score_breakdown": {
        "confirmed_count": 3,
        "repeat_weeks": 2,
        "zone_concentration": 0.75,
        "process_risk_weight": 1.0
      },
      "generated_at": "2026-05-28 18:00:00"
    }
  ]
}
```

생성된 교육 추천 데이터가 없는 경우 아래 오류를 반환함.

```text
404 Education recommendations not found
```

---

## 교육 추천 생성

### `POST /api/recommendations/generate`

관리자가 `confirmed`로 확정한 실제 위반 데이터를 기반으로 교육 추천 결과를 생성함.

### 요청 예시

```http
POST /api/recommendations/generate?quarter=2026-Q2
```

### 생성 기준

교육 추천은 아래 요소를 기준으로 PPE 유형·구역별 우선순위를 계산하고, 상위 추천 항목을 생성함.

| 기준                    | 설명                           |
| --------------------- | ---------------------------- |
| `confirmed_count`     | 해당 PPE 유형·구역에서 확정된 위반 건수     |
| `repeat_weeks`        | 확정 위반이 발생한 주차 수              |
| `zone_concentration`  | 전체 확정 위반 중 해당 유형·구역이 차지하는 비율 |
| `process_risk_weight` | 공정 위험도 가중치                   |

현재 직접 생성되는 교육 주제 예시는 다음과 같음.

| PPE 유형   | 교육 주제 예시                                |
| -------- | --------------------------------------- |
| `helmet` | 프레스 구역 안전모 착용 기준 및 착용 전 점검 절차 교육        |
| `vest`   | 자재 이동 구역 안전조끼 착용 필요성과 작업 구역 내 시인성 확보 교육 |

확정 위반 이벤트가 없어 추천 항목을 생성할 수 없는 경우 아래 오류를 반환함.

```text
404 No confirmed events found for recommendation generation
```

---

# Broadcast Settings API

## 경고 방송 설정 조회

### `GET /api/broadcast/settings`

경고 방송 실행 서비스에서 사용하는 현재 설정을 조회함.

### 응답 예시

```json
{
  "enabled": true,
  "default_language": "ko",
  "cooldown_sec": 30,
  "templates": [
    {
      "ppe_type": "helmet",
      "zone_name": "",
      "language": "ko",
      "message": "해당 작업 구역의 작업자는 안전모 착용 상태를 확인해 주세요."
    }
  ]
}
```

### 설정 항목

| 항목                 | 설명                       |
| ------------------ | ------------------------ |
| `enabled`          | 경고 방송 전체 사용 여부           |
| `default_language` | 기본 방송 언어                 |
| `cooldown_sec`     | 동일 상황 반복 방송 제한 시간        |
| `templates`        | PPE 유형·구역·언어별 메시지 템플릿 목록 |

---

## 경고 방송 설정 저장

### `PUT /api/broadcast/settings`

경고 방송 설정과 메시지 템플릿 목록을 저장함.

### 요청 예시

```http
PUT /api/broadcast/settings
```

```json
{
  "enabled": true,
  "default_language": "ko",
  "cooldown_sec": 30,
  "templates": [
    {
      "ppe_type": "helmet",
      "zone_name": "프레스 구역",
      "language": "ko",
      "message": "프레스 구역 작업자는 안전모 착용 상태를 확인해 주세요."
    },
    {
      "ppe_type": "helmet",
      "zone_name": "",
      "language": "en",
      "message": "Workers in this area, please check your helmet."
    }
  ]
}
```

저장 완료 후에는 저장된 설정값을 다시 조회하여 응답으로 반환함.

실제 경고 메시지 선택, cooldown 처리, 터미널 로그 출력 및 TTS 음성 출력은 `backend/services/warning_broadcast_service.py`에서 수행함.

---

# Static Media

후보 이벤트 검토용 썸네일과 영상 참조 파일은 `storage` 디렉터리 아래에 저장되며, FastAPI는 `/storage` 경로를 정적 파일 경로로 제공함.

## 저장 경로

| 경로                                    | 설명                    |
| ------------------------------------- | --------------------- |
| `storage/candidate_events/thumbnails` | 후보 이벤트 썸네일 이미지        |
| `storage/candidate_events/clips`      | 후보 이벤트 영상 클립 또는 참조 영상 |

## 접근 예시

프로젝트 내 저장 경로:

```text
storage/candidate_events/thumbnails/EVT_20260519160059_b95d63.jpg
```

브라우저 또는 프론트엔드 접근 URL:

```text
http://127.0.0.1:8000/storage/candidate_events/thumbnails/EVT_20260519160059_b95d63.jpg
```

파일이 존재하면 `200`으로 반환하고, 존재하지 않으면 `404`를 반환함.

생성되는 이미지와 영상 파일은 로컬 실행 산출물이므로 Git에 포함하지 않고, 디렉터리 유지를 위한 `.gitkeep`만 관리함.

---

# 테스트 방법

## 재검토 API 회귀 테스트

`PUT /api/events/{event_id}/review`의 성공·거부·오탐 처리 흐름은 아래 테스트 스크립트로 확인할 수 있음.

```bash
python backend/db/init_db.py
python -m backend.api.test_event_rereview_api
```

### 확인 항목

* `hold` 상태 이벤트를 `confirmed`로 재검토할 수 있는지 확인
* `second_review_needed = true`인 이벤트를 재검토할 수 있는지 확인
* 기존 검토 결과가 없는 이벤트의 재검토 요청이 거부되는지 확인
* 재검토 대상이 아닌 이벤트의 요청이 `409`로 거부되는지 확인
* `false_positive` 재검토 시 후보 이벤트가 삭제되고 오탐 집계가 반영되는지 확인

정상 실행 시 마지막에 아래 메시지를 확인할 수 있음.

```text
[OK] event re-review regression tests passed.
```

## API 기본 흐름 확인

1. DB 초기화

```bash
python backend/db/init_db.py
```

2. 필요 시 테스트 이벤트 추가

```bash
python backend/db/seed_events.py
```

3. 서버 실행

```bash
python -m uvicorn backend.api.main:app --reload
```

4. Swagger 접속

```text
http://127.0.0.1:8000/docs
```

---

## 최초 검토 및 재검토 확인

아래 순서로 실행함.

```text
GET  /api/events
POST /api/events/EVT_0001/review       # review_result = hold
PUT  /api/events/EVT_0001/review       # review_result = confirmed
GET  /api/events/EVT_0001
```

정상 동작 시 아래를 확인할 수 있음.

* 최초 검토 이후 이벤트 상태가 `hold`로 변경됨
* 재검토 이후 이벤트 상태가 `confirmed`로 변경됨
* 검토 담당자 및 검토 결과가 재검토 요청값으로 갱신됨

---

## 통계 및 교육 추천 확인

먼저 후보 이벤트 중 일부를 `confirmed`로 검토한 후 아래 순서로 실행함.

```text
POST /api/stats/generate?quarter=2026-Q2
GET  /api/stats?quarter=2026-Q2

POST /api/recommendations/generate?quarter=2026-Q2
GET  /api/recommendations?quarter=2026-Q2
```

정상 동작 시 확정 위반 건수를 기반으로 분기별 통계와 교육 추천 결과를 확인할 수 있음.

---

## 방송 설정 확인

```text
GET /api/broadcast/settings
PUT /api/broadcast/settings
GET /api/broadcast/settings
```

저장 이후 다시 조회했을 때 `enabled`, `default_language`, `cooldown_sec`, `templates` 값이 전달한 요청과 일치하면 정상 동작임.

---

## 정적 미디어 확인

서버 실행 후 PowerShell에서 아래 명령어를 실행함.

```powershell
curl.exe -o NUL -w "%{http_code}`n" http://127.0.0.1:8000/storage/candidate_events/thumbnails/nonexistent.jpg
```

없는 파일에 대해 `404`가 반환되면 정적 경로가 정상 등록된 상태임.

실제 썸네일 파일이 존재하는 경우 해당 파일명으로 다시 호출하여 `200` 응답을 확인할 수 있음.

---

# 현재 구현 범위 및 후속 확장

## 현재 구현 범위

* 후보 이벤트 전체 목록 및 단건 조회
* 관리자 최초 검토 및 재검토 처리
* 오탐 이벤트 비식별 집계 후 상세 삭제 처리
* 확정 위반 데이터 기반 분기별 통계 생성 및 조회
* 확정 위반 데이터 기반 교육 추천 생성 및 조회
* 경고 방송 설정 조회 및 저장
* 후보 이벤트 썸네일·영상 참조 파일 정적 제공

## 후속 확장 대상

* AI 탐지 코드와 후보 이벤트 생성 서비스의 실제 연동
* 안전조끼 미착용 이벤트 생성 흐름 연결
* 이벤트 구간 기준 실제 짧은 영상 클립 생성
* 서버 측 후보 이벤트 필터링 및 페이지네이션
* 방송 실행 이력 저장 및 조회
* 지속 가능한 cooldown 상태 관리

---

## 주의 사항

* 같은 이벤트에 대해 `POST /api/events/{event_id}/review`를 두 번 호출하면 `409 Review already exists`를 반환함.
* 재검토는 `hold` 상태이거나 기존 검토 결과의 `second_review_needed = true`인 이벤트에 대해서만 가능함.
* `false_positive`로 처리된 후보 이벤트는 비식별 오탐 집계에 반영된 후 상세 이벤트에서 삭제됨.
* 통계 및 교육 추천은 별도의 생성 API를 호출한 후 조회 가능함.
* 현재 후보 이벤트 목록의 화면 필터링은 프론트엔드에서 수행하며, 서버 측 filter query parameter는 후속 확장 범위임.
* 로컬 DB 파일과 생성된 썸네일·영상 파일은 Git에 포함하지 않음.
