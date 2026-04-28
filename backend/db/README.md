# PPE DB 초기 구축

이 폴더는 PPE 분석 시스템의 초기 SQLite DB 구조와 테스트용 DB 접근 코드를 포함한다.

현재 단계에서는 전체 백엔드 서버를 구현하기 전, 후보 이벤트 저장 구조와 담당자 검토 결과 저장 흐름을 먼저 검증하는 것을 목적으로 한다.

---

## 파일 구성

| 파일 | 설명 |
|---|---|
| `schema.sql` | DB 테이블 생성 SQL |
| `seed.sql` | 초기 테스트용 더미 데이터 |
| `init_db.py` | SQLite DB 생성 및 초기화 스크립트 |
| `check_db.py` | DB 테이블 및 더미 데이터 조회 확인 스크립트 |
| `event_repository.py` | 후보 이벤트 및 검토 결과 DB 접근 함수 |
| `test_event_repository.py` | repository 함수 동작 확인용 테스트 스크립트 |

---

## 생성되는 DB 파일

`init_db.py`를 실행하면 아래 DB 파일이 생성된다.

```text
backend/db/ppe_system.db
```

해당 파일은 로컬 실행 시 생성되는 파일이므로 GitHub에는 업로드하지 않는다.  
`.gitignore`에서 `backend/db/*.db` 규칙으로 제외한다.

---

## 실행 순서

프로젝트 루트에서 아래 명령어를 실행한다.

```bash
python backend/db/init_db.py
python backend/db/check_db.py
python backend/db/test_event_repository.py
```

Windows 환경에서 `python` 명령어가 동작하지 않으면 다음 명령어를 사용한다.

```bash
py backend/db/init_db.py
py backend/db/check_db.py
py backend/db/test_event_repository.py
```

---

## 실행 결과 확인 기준

### 1. DB 초기화

```bash
python backend/db/init_db.py
```

정상 실행 시 다음과 유사한 메시지가 출력된다.

```text
Database initialized successfully: .../backend/db/ppe_system.db
```

### 2. DB 조회 확인

```bash
python backend/db/check_db.py
```

정상 실행 시 다음 항목을 확인할 수 있다.

- `camera_info` 테이블
- `candidate_event` 테이블
- `event_review` 테이블
- `CAM_001`, `CAM_002` 카메라 더미 데이터
- `EVT_0001`, `EVT_0002`, `EVT_0003` 후보 이벤트 더미 데이터

### 3. Repository 함수 테스트

```bash
python backend/db/test_event_repository.py
```

정상 실행 시 다음 흐름을 확인할 수 있다.

- 후보 이벤트 전체 조회
- 후보 이벤트 단건 조회
- 새 후보 이벤트 삽입
- 담당자 검토 결과 삽입
- 검토 결과 저장 후 `candidate_event.event_status` 갱신 확인

---

## 주요 테이블

| 테이블 | 역할 |
|---|---|
| `camera_info` | 카메라, 촬영 구역, 공정 정보 저장 |
| `candidate_event` | AI가 생성한 PPE 미착용 후보 이벤트 저장 |
| `event_review` | 담당자의 검토 결과 저장 |

---

## 주요 함수

`event_repository.py`에서 제공하는 함수는 다음과 같다.

| 함수 | 설명 |
|---|---|
| `get_all_candidate_events()` | 후보 이벤트 전체 목록 조회 |
| `get_candidate_event_by_id(event_id)` | 특정 후보 이벤트 단건 조회 |
| `insert_candidate_event(event)` | 새 후보 이벤트 저장 |
| `insert_event_review(review)` | 담당자 검토 결과 저장 및 이벤트 상태 갱신 |
| `get_review_by_event_id(event_id)` | 특정 이벤트의 검토 결과 조회 |

---

## 상태값 기준

### `candidate_event.event_status`

| 값 | 의미 |
|---|---|
| `pending` | 검토 대기 |
| `confirmed` | 확정 위반 |
| `false_positive` | 오탐 |
| `hold` | 판단 보류 |

### `event_review.review_result`

| 값 | 의미 |
|---|---|
| `confirmed` | 확정 위반 |
| `false_positive` | 오탐 |
| `hold` | 판단 보류 |

---

## SQLite Boolean 처리

SQLite에서는 Boolean 값을 별도 타입으로 엄격하게 저장하지 않고 `INTEGER`로 저장한다.

| 의미 | 저장값 |
|---|---|
| True | `1` |
| False | `0` |

따라서 현재 DB에서는 다음 필드를 `1` 또는 `0`으로 저장한다.

- `person_detected`
- `ppe_detected`
- `confirmed_violation`
- `second_review_needed`

---

## 주의 사항

- `ppe_system.db`는 로컬에서 생성되는 파일이므로 GitHub에 커밋하지 않는다.
- `init_db.py`는 `schema.sql`과 `seed.sql`을 실행하여 DB를 초기화한다.
- 같은 더미 데이터를 여러 번 삽입해도 오류가 나지 않도록 `seed.sql`에는 `INSERT OR IGNORE`를 사용한다.
- `test_event_repository.py`는 테스트용 이벤트 ID인 `EVT_TEST_0001`과 `RV_TEST_0001`을 사용한다.
- 테스트를 완전히 초기 상태에서 다시 실행하려면 `ppe_system.db`를 삭제한 뒤 `init_db.py`를 다시 실행하면 된다.

---

## 향후 확장 예정

현재 DB 구축 단계에서는 핵심 테이블 3개만 우선 구현했다.

향후 다음 테이블 및 기능을 추가할 수 있다.

| 항목 | 설명 |
|---|---|
| `quarterly_stats` | 분기별 누적 통계 저장 |
| `education_recommendation` | 분기별 교육 추천 결과 저장 |
| FastAPI 또는 Flask 연동 | 프론트엔드 대시보드와 REST API로 연결 |
| 후보 이벤트 필터링 | PPE 유형, 구역, 상태, 날짜 기준 조회 |
| 검토 결과 기반 통계 계산 | 확정 위반, 오탐, 보류 건수 집계 |