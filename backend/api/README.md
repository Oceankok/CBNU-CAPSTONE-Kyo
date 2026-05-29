# PPE API 초기 구현

이 폴더는 PPE 분석 시스템의 초기 FastAPI 서버 코드를 포함함.

현재 단계에서는 DB에 저장된 후보 이벤트를 조회하고, 담당자 검토 결과를 저장하는 최소 API를 제공함.

---

## 파일 구성

| 파일 | 설명 |
|---|---|
| `main.py` | FastAPI 서버 및 후보 이벤트 API 엔드포인트 정의 |
| `__init__.py` | `backend.api` 패키지 인식용 파일 |

---

## 실행 전 준비

API 실행 전 DB 파일이 먼저 생성되어 있어야 함.

프로젝트 루트에서 아래 명령어 실행.

```bash
python backend/db/init_db.py
```

Windows 환경에서 `python` 명령어가 동작하지 않을 경우 아래 명령어 사용 가능.

```bash
py backend/db/init_db.py
```

---

## API 서버 실행

프로젝트 루트에서 아래 명령어 실행.

```bash
python -m uvicorn backend.api.main:app --reload
```

Windows 환경에서 `python` 명령어가 동작하지 않을 경우 아래 명령어 사용 가능.

```bash
py -m uvicorn backend.api.main:app --reload
```

정상 실행 시 아래 주소에서 API 서버 확인 가능.

```text
http://127.0.0.1:8000
```

Swagger 문서는 아래 주소에서 확인 가능.

```text
http://127.0.0.1:8000/docs
```

---

## 제공 API

| Method | Endpoint | 설명 |
|---|---|---|
| GET | `/` | API 서버 상태 확인 |
| GET | `/api/events` | 후보 이벤트 전체 목록 조회 |
| GET | `/api/events/{event_id}` | 후보 이벤트 단건 조회 |
| POST | `/api/events/{event_id}/review` | 후보 이벤트 최초 검토 결과 저장 |
| PUT | `/api/events/{event_id}/review` | 보류 또는 2차 검토 대상 이벤트 재검토 결과 갱신 |

---

## 응답 예시

### 후보 이벤트 전체 조회

```bash
GET /api/events
```

응답 예시.

```json
{
  "total": 3,
  "items": [
    {
      "event_id": "EVT_0001",
      "camera_id": "CAM_001",
      "zone_name": "프레스 구역",
      "process_type": "유압 성형",
      "ppe_type": "helmet",
      "duration_sec": 3,
      "ai_confidence": 0.87,
      "event_status": "pending"
    }
  ]
}
```

---

### 후보 이벤트 단건 조회

```bash
GET /api/events/EVT_0001
```

응답 예시.

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

---

### 담당자 검토 결과 저장

```bash
POST /api/events/EVT_0001/review
```

요청 Body 예시.

```json
{
  "reviewer_id": "admin01",
  "review_result": "confirmed",
  "review_reason_code": "confirmed_no_helmet",
  "review_comment": "실제 안전모 미착용",
  "second_review_needed": false
}
```

정상 저장 시 `candidate_event.event_status`가 `review_result` 값으로 갱신됨.

---

### 담당자 재검토 결과 갱신

```bash
PUT /api/events/EVT_0001/review
```

`hold` 상태이거나 기존 검토 결과의 `second_review_needed`가 `true`인 이벤트에 대해 재검토 결과를 제출함.

요청 Body 예시.

```json
{
  "reviewer_id": "admin02",
  "review_result": "confirmed",
  "review_reason_code": "confirmed_no_helmet",
  "review_comment": "재검토 결과 실제 위반 확인",
  "second_review_needed": false
}
```

재검토가 허용되는 조건은 다음과 같음.

| 기존 상태 | 재검토 가능 여부 |
|---|---|
| `event_status = hold` | 가능 |
| 기존 검토 결과의 `second_review_needed = true` | 가능 |
| `event_status = confirmed`이고 `second_review_needed = false` | 불가능 |
| 기존 검토 결과가 없는 이벤트 | 불가능 |

`confirmed` 또는 `hold`로 재검토한 경우 기존 `event_review` 행을 갱신하고, `candidate_event.event_status`도 재검토 결과에 맞게 갱신함.

정상 응답 예시.

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

재검토 결과가 `false_positive`인 경우에는 기존 오탐 처리 정책에 따라 비식별 집계에 반영한 뒤 후보 이벤트를 삭제함.

```json
{
  "status": "ok",
  "message": "False positive event deleted after re-review",
  "event_id": "EVT_0001",
  "review_result": "false_positive"
}
```
---


## 검토 결과 처리 기준

### 최초 검토: `POST /api/events/{event_id}/review`

최초 검토 결과에 따라 후보 이벤트를 다음과 같이 처리함.

| `review_result` | 처리 방식 |
|---|---|
| `confirmed` | 확정 위반으로 처리하고 `candidate_event.event_status`를 `confirmed`로 갱신함 |
| `hold` | 판단 보류 상태로 처리하고 `candidate_event.event_status`를 `hold`로 갱신함 |
| `false_positive` | 비식별 오탐 집계에 반영한 뒤 후보 이벤트를 삭제함 |

동일 이벤트에 기존 검토 결과가 이미 존재하는 경우 최초 검토를 다시 저장할 수 없으며, `409 Review already exists`를 반환함.

### 재검토: `PUT /api/events/{event_id}/review`

재검토는 기존 이벤트가 `hold` 상태이거나, 기존 검토 결과에 `second_review_needed = true`가 저장된 경우에만 허용함.

| `review_result` | 처리 방식 |
|---|---|
| `confirmed` | 기존 `event_review`를 갱신하고 `candidate_event.event_status`를 `confirmed`로 갱신함 |
| `hold` | 기존 `event_review`를 갱신하고 `candidate_event.event_status`를 `hold`로 갱신함 |
| `false_positive` | 비식별 오탐 집계에 반영한 뒤 후보 이벤트를 삭제함 |

재검토 대상이 아닌 이벤트에 PUT 요청을 보내면 `409 Event is not eligible for re-review`를 반환함.  
기존 검토 결과가 없는 이벤트에 PUT 요청을 보내면 `409 Review does not exist`를 반환함.

## 테스트 방법

1. DB 초기화

```bash
python backend/db/init_db.py
```

2. API 서버 실행

```bash
python -m uvicorn backend.api.main:app --reload
```

3. Swagger 접속

```text
http://127.0.0.1:8000/docs
```

4. 아래 API 순서대로 테스트

```text
GET /api/events
GET /api/events/{event_id}
POST /api/events/{event_id}/review
GET /api/events/{event_id}
```

마지막 `GET /api/events/{event_id}`에서 `review` 값이 추가되고, `event_status`가 변경되면 정상 동작임.

### 재검토 API 테스트

1. 최초 검토 결과를 `hold`로 저장함.

```http
POST /api/events/EVT_0001/review
```

```json
{
  "reviewer_id": "admin01",
  "review_result": "hold",
  "review_reason_code": "needs_additional_check",
  "review_comment": "영상 확인 필요",
  "second_review_needed": false
}
```

2. 동일 이벤트를 `confirmed`로 재검토함.

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

3. 단건 조회 API로 갱신 결과를 확인함.

```http
GET /api/events/EVT_0001
```

정상 동작 시 아래 항목을 확인할 수 있음.

- `event.event_status`가 `confirmed`로 변경됨
- `review.reviewer_id`가 재검토 담당자 값으로 변경됨
- `review.review_result`가 `confirmed`로 변경됨
- `review.confirmed_violation`이 `1`로 변경됨

추가 확인 항목:

- `confirmed`이면서 `second_review_needed = false`인 이벤트의 재검토 요청은 `409`를 반환함
- 기존 검토 결과가 없는 이벤트의 재검토 요청은 `409`를 반환함
- `hold` 이벤트를 `false_positive`로 재검토하면 이벤트가 삭제됨

---

## 분기별 통계 조회 API

### `GET /api/stats`

분기별 통계 데이터를 조회함.

예시:

```http
GET /api/stats?quarter=2026-Q2
```

응답에는 다음 데이터가 포함됨.

- 분기 요약 통계
- PPE 유형별 확정 위반 수 및 우선순위 점수
- 구역별 확정 위반 수 및 우선순위 점수
- 최근 분기별 helmet/vest 위반 추이

응답 예시:

```json
{
  "quarter": "2026-Q2",
  "summary": {
    "quarter": "2026-Q2",
    "candidate_count": 145,
    "confirmed_count": 98,
    "false_positive_count": 32,
    "hold_count": 15
  },
  "by_ppe_type": [
    {
      "ppe_type": "helmet",
      "confirmed_count": 60,
      "priority_score": 8.6
    },
    {
      "ppe_type": "vest",
      "confirmed_count": 38,
      "priority_score": 5.2
    }
  ],
  "by_zone": [
    {
      "zone_name": "프레스 구역",
      "confirmed_count": 45,
      "priority_score": 8.6
    }
  ],
  "trend": [
    {
      "quarter": "2025-Q3",
      "helmet": 40,
      "vest": 25
    }
  ]
}
```

---

## 교육 추천 조회 API

### `GET /api/recommendations`

분기별 교육 추천 데이터를 조회함.

예시:

```http
GET /api/recommendations?quarter=2026-Q2
```

응답에는 다음 데이터가 포함됨.

- 추천 순위
- PPE 유형
- 발생 구역
- 교육 주제
- 우선순위 점수
- 점수 산정 근거

응답 예시:

```json
{
  "quarter": "2026-Q2",
  "generated_at": "2026-06-30 18:00:00",
  "items": [
    {
      "recommendation_id": "EDU_2026Q2_01",
      "recommendation_rank": 1,
      "ppe_type": "helmet",
      "zone_name": "프레스 구역",
      "education_topic": "안전모 착용 기준 및 착용 전 점검 절차 교육",
      "priority_score": 8.6,
      "score_breakdown": {
        "confirmed_count": 60,
        "repeat_weeks": 5,
        "zone_concentration": 0.61,
        "process_risk_weight": 1.0
      },
      "generated_at": "2026-06-30 18:00:00"
    }
  ]
}
```

---

## 참고 사항

현재 `/api/stats`와 `/api/recommendations`는 프론트엔드 Mock 데이터 구조와 유사한 형태로 응답하도록 구성함.

`false_positive_count`는 오탐 이벤트 상세 기록을 장기 보관하지 않고, `false_positive_aggregate`에 저장된 비식별 집계값을 기준으로 계산함.

이번 단계에서는 분기별 통계 및 교육 추천 더미 데이터를 DB에 저장하고 조회하는 구조를 우선 구현함. 실제 후보 이벤트와 담당자 검토 결과를 기반으로 통계를 자동 계산하고 교육 추천을 생성하는 로직은 이후 단계에서 확장 예정임.

---

## 경고 방송 설정 API

### `GET /api/broadcast/settings`

경고 방송 설정을 조회함.

예시:

```http
GET /api/broadcast/settings
```

응답 예시:

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

---

### `PUT /api/broadcast/settings`

경고 방송 설정을 저장함.

예시:

```http
PUT /api/broadcast/settings
```

요청 Body 예시:

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

응답은 저장된 설정을 다시 반환함.

---

## 정적 미디어 파일 제공

후보 이벤트 검토용 썸네일과 영상 클립은 `storage` 디렉터리 아래에 저장되며, FastAPI에서 `/storage` 경로로 정적 파일을 제공함.

예시 파일 경로:

```text
storage/candidate_events/thumbnails/EVT_xxxx.jpg
```

브라우저 접근 URL:

```text
http://127.0.0.1:8000/storage/candidate_events/thumbnails/EVT_xxxx.jpg
```

없는 파일에 접근하면 404를 반환하고, 실제 파일이 존재하면 200으로 파일을 반환함.

이 경로는 프론트엔드 ReviewDetailPage에서 후보 이벤트의 `thumbnail_path`, `video_clip_path`를 표시할 때 사용함.

## 참고 사항

이 API는 경고 방송 설정을 저장하고 조회하기 위한 API임.

실제 경고 방송 실행, 터미널 출력, TTS 음성 출력은 별도 기능에서 구현함.

---

## 주의 사항

- 같은 이벤트에 대해 `POST /api/events/{event_id}/review`를 두 번 호출하면 `409 Review already exists` 응답을 반환함.
- 기존 검토 결과가 있는 이벤트라도 `hold` 상태이거나 `second_review_needed = true`인 경우에는 `PUT /api/events/{event_id}/review`로 재검토 가능함.
- 재검토 대상이 아닌 이벤트에 PUT 요청을 보내면 `409 Event is not eligible for re-review` 응답을 반환함.
- 존재하지 않는 이벤트 ID를 조회하거나 검토 결과를 저장하면 `404 Event not found` 응답을 반환함.
- `review_result`는 `confirmed`, `false_positive`, `hold` 중 하나만 허용함.
- `false_positive`로 처리된 이벤트는 비식별 오탐 집계에 반영된 후 상세 후보 이벤트에서 삭제됨.
- 로컬 DB 파일 `ppe_system.db`는 GitHub에 업로드하지 않음.