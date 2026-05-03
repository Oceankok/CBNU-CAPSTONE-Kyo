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
| POST | `/api/events/{event_id}/review` | 후보 이벤트 담당자 검토 결과 저장 |

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

## 후보 이벤트 필터링

`GET /api/events`는 query parameter를 통해 후보 이벤트 목록을 조건별로 조회할 수 있음.

### 사용 가능한 필터

| Query Parameter | 설명 | 예시 |
|---|---|---|
| `ppe_type` | PPE 유형 기준 필터 | `helmet`, `vest` |
| `event_status` | 이벤트 검토 상태 기준 필터 | `pending`, `confirmed`, `false_positive`, `hold` |
| `zone_name` | 구역 이름 기준 필터 | `프레스 구역` |
| `date_from` | 조회 시작 날짜 | `2026-04-01` |
| `date_to` | 조회 종료 날짜 | `2026-04-30` |
| `min_confidence` | 최소 AI 신뢰도 기준 필터 | `0.85` |

---

### 필터 사용 예시

#### PPE 유형 기준 조회

```text
GET /api/events?ppe_type=helmet
```

`ppe_type`이 `helmet`인 후보 이벤트만 반환함.

---

#### 이벤트 상태 기준 조회

```text
GET /api/events?event_status=pending
```

검토 대기 상태인 후보 이벤트만 반환함.

---

#### 구역 기준 조회

```text
GET /api/events?zone_name=프레스 구역
```

`프레스 구역`에서 발생한 후보 이벤트만 반환함.

---

#### 날짜 범위 기준 조회

```text
GET /api/events?date_from=2026-04-01&date_to=2026-04-30
```

해당 날짜 범위 안에서 발생한 후보 이벤트만 반환함.

---

#### 신뢰도 기준 조회

```text
GET /api/events?min_confidence=0.85
```

AI 신뢰도가 `0.85` 이상인 후보 이벤트만 반환함.

---

#### 복합 조건 조회

```text
GET /api/events?ppe_type=helmet&event_status=pending&min_confidence=0.8
```

`helmet` 미착용 후보 중 검토 대기 상태이며, AI 신뢰도가 `0.8` 이상인 이벤트만 반환함.

---

### 필터 동작 기준

- query parameter가 없으면 기존처럼 전체 후보 이벤트 목록 반환
- 여러 query parameter를 함께 사용하면 `AND` 조건으로 필터링
- 조건에 맞는 후보 이벤트가 없으면 빈 목록 반환

```json
{
  "total": 0,
  "items": []
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

## review_result 값 기준

| 값 | 의미 |
|---|---|
| `confirmed` | 확정 위반 |
| `false_positive` | 오탐 |
| `hold` | 판단 보류 |

---

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

5. 필터 조건 테스트

```text
GET /api/events?ppe_type=helmet
GET /api/events?event_status=pending
GET /api/events?zone_name=프레스 구역
GET /api/events?min_confidence=0.85
GET /api/events?ppe_type=helmet&event_status=pending&min_confidence=0.8
```

각 조건에 맞는 후보 이벤트만 반환되면 정상 동작임.

---

## 주의 사항

- 같은 이벤트에 대해 검토 결과를 두 번 저장하면 `409 Review already exists` 응답 반환.
- 존재하지 않는 이벤트 ID를 조회하거나 검토 결과를 저장하면 `404 Event not found` 응답 반환.
- `review_result`는 `confirmed`, `false_positive`, `hold` 중 하나만 허용함.
- 로컬 DB 파일 `ppe_system.db`는 GitHub에 업로드하지 않음.