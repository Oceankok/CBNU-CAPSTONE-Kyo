# Backend Database

이 폴더는 PPE 분석 시스템의 SQLite 데이터베이스 구조와 DB 접근 계층 코드를 포함함.

현재 DB는 AI가 생성한 PPE 미착용 후보 이벤트를 저장하고, 관리자 검토 및 재검토 결과를 반영하며, 확정 위반 데이터를 기반으로 통계와 교육 추천을 생성하는 흐름을 지원함. 또한 경고 방송 설정과 메시지 템플릿을 저장하여 방송 실행 서비스에서 사용할 수 있도록 구성함.

---

## 파일 구성

| 파일                         | 설명                                 |
| -------------------------- | ---------------------------------- |
| `schema.sql`               | DB 테이블 생성 SQL                      |
| `seed.sql`                 | 기본 테스트용 초기 데이터 삽입 SQL              |
| `init_db.py`               | SQLite DB 생성 및 초기화 스크립트            |
| `seed_events.py`           | 추가 테스트용 후보 이벤트 삽입 스크립트             |
| `check_db.py`              | DB 테이블 및 저장 데이터 조회 확인 스크립트         |
| `event_repository.py`      | 후보 이벤트, 검토, 통계, 추천, 방송 설정 DB 접근 함수 |
| `test_event_repository.py` | repository 함수 동작 확인용 테스트 스크립트      |

---

## 생성되는 DB 파일

`init_db.py`를 실행하면 아래 위치에 SQLite DB 파일이 생성됨.

```text
backend/db/ppe_system.db
```

해당 파일은 로컬 실행 및 테스트 과정에서 생성되는 런타임 데이터이므로 Git에 포함하지 않음.

```gitignore
backend/db/*.db
```

---

## DB 초기화 및 확인

프로젝트 루트에서 아래 순서로 실행함.

```bash
python backend/db/init_db.py
python backend/db/check_db.py
```

Windows 환경에서 `python` 명령어가 동작하지 않을 경우:

```bash
py backend/db/init_db.py
py backend/db/check_db.py
```

추가 테스트용 후보 이벤트가 필요한 경우 아래 스크립트를 실행함.

```bash
python backend/db/seed_events.py
```

---

## 데이터 처리 흐름

현재 DB는 아래 흐름을 기준으로 사용됨.

```text
AI PPE 미착용 탐지
→ candidate_event 후보 이벤트 저장
→ 관리자 최초 검토 또는 재검토
→ confirmed / hold / false_positive 결과 반영
→ confirmed 데이터 기반 통계 생성
→ confirmed 데이터 기반 교육 추천 생성
```

경고 방송 설정은 별도 흐름으로 저장되며, 후보 이벤트 생성 이후 방송 실행 서비스가 해당 설정을 조회하여 사용함.

```text
방송 설정 UI
→ broadcast_setting 및 broadcast_message_template 저장
→ 후보 이벤트 발생
→ 방송 실행 서비스가 설정 조회
→ 메시지 선택 및 TTS 실행
```

---

# 테이블 구성

## 1. 이벤트 및 검토 테이블

| 테이블                        | 역할                        |
| -------------------------- | ------------------------- |
| `camera_info`              | 카메라 ID, 촬영 구역, 공정 정보 저장   |
| `candidate_event`          | AI가 생성한 PPE 미착용 후보 이벤트 저장 |
| `event_review`             | 관리자의 확정 위반·보류 검토 결과 저장    |
| `false_positive_aggregate` | 오탐 이벤트의 비식별 집계값 저장        |

---

## 2. 통계 및 교육 추천 테이블

| 테이블                        | 역할                              |
| -------------------------- | ------------------------------- |
| `quarterly_summary`        | 분기별 후보 이벤트, 확정 위반, 오탐, 보류 건수 요약 |
| `quarterly_ppe_stats`      | PPE 유형별 확정 위반 수 및 우선순위 점수 저장    |
| `quarterly_zone_stats`     | 구역별 확정 위반 수 및 우선순위 점수 저장        |
| `quarterly_trend_stats`    | 분기별 안전모·안전조끼 확정 위반 추이 저장        |
| `education_recommendation` | 분기별 교육 추천 결과와 점수 산정 근거 저장       |

---

## 3. 경고 방송 설정 테이블

| 테이블                          | 역할                              |
| ---------------------------- | ------------------------------- |
| `broadcast_setting`          | 방송 사용 여부, 기본 언어, cooldown 설정 저장 |
| `broadcast_message_template` | PPE 유형·구역·언어별 방송 메시지 템플릿 저장     |

---

# 주요 데이터 구조

## `camera_info`

후보 이벤트가 발생한 구역 및 공정 정보를 연결하기 위한 기준 테이블임.

주요 데이터 예시:

| 필드             | 설명      |
| -------------- | ------- |
| `camera_id`    | 카메라 식별자 |
| `zone_name`    | 작업 구역명  |
| `process_type` | 공정 유형   |

후보 이벤트 조회 시 `candidate_event.camera_id`와 연결하여 `zone_name`, `process_type`을 함께 반환함.

---

## `candidate_event`

AI가 탐지한 PPE 미착용 의심 상황을 관리자 검토 전까지 저장하는 테이블임.

| 필드                   | 설명                          |
| -------------------- | --------------------------- |
| `event_id`           | 후보 이벤트 식별자                  |
| `camera_id`          | 이벤트가 발생한 카메라 ID             |
| `tracking_id`        | 탐지 객체 추적 ID                 |
| `ppe_type`           | PPE 유형. 예: `helmet`, `vest` |
| `timestamp_start`    | 이벤트 시작 시각                   |
| `timestamp_end`      | 이벤트 종료 시각                   |
| `duration_sec`       | 이벤트 지속 시간                   |
| `frame_sample_count` | 판단에 사용된 프레임 수               |
| `thumbnail_path`     | 검토용 썸네일 상대 경로               |
| `video_clip_path`    | 영상 클립 또는 참조 영상 상대 경로        |
| `ai_confidence`      | AI 탐지 신뢰도                   |
| `person_detected`    | 사람 탐지 여부                    |
| `ppe_detected`       | PPE 착용 탐지 여부                |
| `model_version`      | 사용 모델 버전                    |
| `event_status`       | 검토 상태                       |

### `event_status` 값

| 값           | 의미               |
| ----------- | ---------------- |
| `pending`   | 관리자 검토 대기        |
| `confirmed` | 실제 위반으로 확정       |
| `hold`      | 추가 확인이 필요한 보류 상태 |

`false_positive`는 상세 이벤트를 유지하지 않고 오탐 집계에 반영한 뒤 후보 이벤트를 삭제하므로, 처리 완료 후 `candidate_event`에 상세 행이 남지 않음.

---

## `event_review`

관리자가 `confirmed` 또는 `hold`로 판단한 검토 결과를 저장함.

| 필드                     | 설명              |
| ---------------------- | --------------- |
| `review_id`            | 검토 결과 식별자       |
| `event_id`             | 검토 대상 후보 이벤트 ID |
| `reviewer_id`          | 검토 담당자 ID       |
| `review_result`        | 검토 결과           |
| `review_reason_code`   | 검토 사유 코드        |
| `review_time`          | 검토 시각           |
| `review_comment`       | 검토 의견           |
| `confirmed_violation`  | 확정 위반 여부        |
| `second_review_needed` | 2차 검토 필요 여부     |

### `review_result` 값

| 값                | 의미       | 저장 처리                           |
| ---------------- | -------- | ------------------------------- |
| `confirmed`      | 실제 위반 확인 | 검토 행 저장 및 이벤트 상태 `confirmed` 갱신 |
| `hold`           | 판단 보류    | 검토 행 저장 및 이벤트 상태 `hold` 갱신      |
| `false_positive` | 오탐       | 상세 검토 행을 유지하지 않고 오탐 집계 후 이벤트 삭제 |

---

## `false_positive_aggregate`

관리자가 오탐으로 판단한 이벤트는 상세 기록을 유지하지 않고, 다음 기준으로 집계값만 저장함.

```text
분기 + 구역 + PPE 유형
```

| 필드                     | 설명        |
| ---------------------- | --------- |
| `aggregate_id`         | 오탐 집계 식별자 |
| `quarter`              | 대상 분기     |
| `zone_name`            | 발생 구역     |
| `ppe_type`             | PPE 유형    |
| `false_positive_count` | 누적 오탐 건수  |
| `updated_at`           | 마지막 갱신 시각 |

이 구조를 통해 상세 이미지나 이벤트 데이터를 장기 보존하지 않으면서, 특정 구역 또는 PPE 유형에서 오탐이 반복되는지 통계적으로 확인할 수 있음.

---

# 검토 및 재검토 처리 기준

## 최초 검토

최초 검토는 API의 아래 endpoint에서 처리함.

```http
POST /api/events/{event_id}/review
```

| 검토 결과            | DB 처리                                                             |
| ---------------- | ----------------------------------------------------------------- |
| `confirmed`      | `event_review` 삽입 후 `candidate_event.event_status = confirmed` 갱신 |
| `hold`           | `event_review` 삽입 후 `candidate_event.event_status = hold` 갱신      |
| `false_positive` | `false_positive_aggregate` 반영 후 `candidate_event` 삭제              |

동일한 이벤트에 이미 검토 결과가 존재하면 최초 검토를 다시 저장하지 않음.

---

## 재검토

재검토는 아래 조건 중 하나를 만족하는 이벤트에 대해서만 허용함.

```text
event_status = hold
또는
기존 event_review.second_review_needed = 1
```

재검토 API:

```http
PUT /api/events/{event_id}/review
```

| 재검토 결과           | DB 처리                                                                |
| ---------------- | -------------------------------------------------------------------- |
| `confirmed`      | 기존 `event_review` 갱신 후 `candidate_event.event_status = confirmed` 갱신 |
| `hold`           | 기존 `event_review` 갱신 후 `candidate_event.event_status = hold` 갱신      |
| `false_positive` | `false_positive_aggregate` 반영 후 `candidate_event` 삭제                 |

`candidate_event`가 삭제되는 경우 FK의 `ON DELETE CASCADE` 정책에 따라 연결된 기존 검토 결과도 함께 삭제됨.

---

# 통계 및 교육 추천 처리

## 분기별 통계

분기별 통계는 지정 분기의 후보 이벤트 및 검토 결과를 기준으로 생성함.

생성 API:

```http
POST /api/stats/generate?quarter=2026-Q2
```

조회 API:

```http
GET /api/stats?quarter=2026-Q2
```

### 통계 반영 기준

| 통계 항목                  | 기준                                    |
| ---------------------- | ------------------------------------- |
| `candidate_count`      | 지정 분기에 저장된 후보 이벤트 수                   |
| `confirmed_count`      | `event_status = confirmed`인 이벤트 수     |
| `hold_count`           | `event_status = hold`인 이벤트 수          |
| `false_positive_count` | `false_positive_aggregate`의 해당 분기 집계값 |
| PPE 유형별 통계             | 확정 위반 이벤트의 PPE 유형별 집계                 |
| 구역별 통계                 | 확정 위반 이벤트의 발생 구역별 집계                  |
| 분기별 추이                 | 확정 위반 이벤트의 PPE 유형별 분기 집계              |

---

## 교육 추천

교육 추천은 관리자 검토 결과 중 `confirmed` 상태인 실제 위반 이벤트만을 기반으로 생성함.

생성 API:

```http
POST /api/recommendations/generate?quarter=2026-Q2
```

조회 API:

```http
GET /api/recommendations?quarter=2026-Q2
```

### 추천 산정 기준

| 기준                    | 설명                          |
| --------------------- | --------------------------- |
| `confirmed_count`     | PPE 유형·구역별 확정 위반 건수         |
| `repeat_weeks`        | 확정 위반이 발생한 주차 수             |
| `zone_concentration`  | 전체 확정 위반 중 해당 PPE 유형·구역의 비율 |
| `process_risk_weight` | 공정 위험도 가중치                  |

현재 추천 생성 로직은 계산된 우선순위 점수를 기준으로 상위 3개 교육 추천을 저장함.

### 현재 동작 관련 주의 사항

현재 통계 생성 로직은 동일 분기의 기존 교육 추천 데이터를 초기화함. 따라서 이미 생성한 교육 추천 결과가 있는 상태에서 통계를 다시 생성한 경우, 최신 교육 추천을 확인하려면 아래 생성 API를 다시 실행해야 함.

```http
POST /api/recommendations/generate?quarter=2026-Q2
```

---

# 경고 방송 설정 저장 구조

경고 방송 실행 서비스는 DB에 저장된 설정을 조회하여 메시지 선택, cooldown 판단, TTS 실행 여부 처리에 사용함.

## 방송 기본 설정

`broadcast_setting`은 기본 설정 1건을 관리함.

| 필드                 | 설명                        |
| ------------------ | ------------------------- |
| `setting_id`       | 설정 식별자. 현재 기본값은 `DEFAULT` |
| `enabled`          | 방송 전체 사용 여부               |
| `default_language` | 기본 방송 언어                  |
| `cooldown_sec`     | 동일 상황 반복 방송 제한 시간         |
| `updated_at`       | 마지막 설정 변경 시각              |

## 방송 메시지 템플릿

`broadcast_message_template`은 PPE 유형·구역·언어별 메시지를 저장함.

| 필드            | 설명                       |
| ------------- | ------------------------ |
| `template_id` | 메시지 템플릿 식별자              |
| `setting_id`  | 연결된 방송 설정 ID             |
| `ppe_type`    | PPE 유형                   |
| `zone_name`   | 특정 구역명. 빈 문자열이면 전체 구역 대상 |
| `language`    | 메시지 언어                   |
| `message`     | 실제 방송 문구                 |

메시지 선택 시 특정 구역 템플릿을 우선 적용하고, 일치하는 특정 구역 메시지가 없으면 `zone_name = ""`인 전체 구역 템플릿을 사용함.

---

# `event_repository.py` 주요 함수

## 후보 이벤트

| 함수                                    | 설명              |
| ------------------------------------- | --------------- |
| `get_all_candidate_events()`          | 전체 후보 이벤트 목록 조회 |
| `get_candidate_event_by_id(event_id)` | 특정 후보 이벤트 단건 조회 |
| `insert_candidate_event(event)`       | 새 후보 이벤트 저장     |
| `delete_candidate_event(event_id)`    | 후보 이벤트 삭제       |

## 관리자 검토

| 함수                                 | 설명                       |
| ---------------------------------- | ------------------------ |
| `insert_event_review(review)`      | 최초 검토 결과 저장 또는 오탐 집계 처리  |
| `update_event_review(review)`      | 기존 검토 결과 재검토 갱신 또는 오탐 처리 |
| `get_review_by_event_id(event_id)` | 특정 이벤트의 검토 결과 조회         |

## 통계 및 교육 추천

| 함수                                            | 설명                     |
| --------------------------------------------- | ---------------------- |
| `get_quarterly_stats(quarter)`                | 생성된 분기별 통계 조회          |
| `generate_quarterly_stats(quarter)`           | 후보 이벤트와 검토 결과 기반 통계 생성 |
| `get_education_recommendations(quarter)`      | 생성된 교육 추천 조회           |
| `generate_education_recommendations(quarter)` | 확정 위반 기반 교육 추천 생성      |

## 경고 방송 설정

| 함수                                  | 설명                   |
| ----------------------------------- | -------------------- |
| `get_broadcast_settings()`          | 현재 경고 방송 설정 및 템플릿 조회 |
| `save_broadcast_settings(settings)` | 방송 설정 및 템플릿 저장       |

---

# SQLite Boolean 처리

SQLite에서는 Boolean 값을 별도 타입으로 엄격하게 저장하지 않고 `INTEGER` 값으로 저장함.

| 의미      | 저장값 |
| ------- | --- |
| `True`  | `1` |
| `False` | `0` |

현재 아래 필드에서 Boolean 의미의 정수값을 사용함.

```text
candidate_event.person_detected
candidate_event.ppe_detected
event_review.confirmed_violation
event_review.second_review_needed
broadcast_setting.enabled
```

---

# 실행 및 검증 방법

## 1. DB 초기화

```bash
python backend/db/init_db.py
```

정상 실행 시 SQLite DB 파일이 생성됨.

```text
backend/db/ppe_system.db
```

---

## 2. 기본 데이터 확인

```bash
python backend/db/check_db.py
```

정상 실행 시 카메라 정보와 기본 후보 이벤트 데이터를 확인할 수 있음.

---

## 3. 추가 테스트 이벤트 삽입

목록·통계·검토 테스트를 위해 추가 이벤트가 필요한 경우 실행함.

```bash
python backend/db/seed_events.py
```

---

## 4. Repository 기본 테스트

```bash
python backend/db/test_event_repository.py
```

테스트 스크립트 범위에 따라 아래 항목을 확인할 수 있음.

* 후보 이벤트 전체 및 단건 조회
* 후보 이벤트 삽입
* 검토 결과 저장
* 이벤트 상태 갱신

재검토 API, 통계·추천 생성, 방송 설정 연동은 Swagger 또는 각 서비스 테스트에서 추가 확인함.

---

# 현재 구현 범위 및 후속 확장

## 현재 구현 범위

* 후보 이벤트 및 미디어 경로 저장 구조
* 관리자 최초 검토 및 재검토 결과 반영
* 오탐 이벤트의 비식별 집계 및 상세 이벤트 삭제
* 확정 위반 기반 분기별 통계 생성 및 조회
* 확정 위반 기반 교육 추천 생성 및 조회
* 경고 방송 설정 및 메시지 템플릿 저장

## 후속 확장 대상

* 서버 측 후보 이벤트 필터링 및 페이지네이션
* 공정별 위험도 가중치의 실제 기준값 적용
* 방송 실행 이력 저장
* cooldown 상태 영구 관리
* 실제 AI 탐지 결과 기반 데이터 축적 및 분석

---

## 주의 사항

* `ppe_system.db`는 로컬 실행 시 생성되는 파일이므로 Git에 포함하지 않음.
* 생성된 후보 이벤트 썸네일 및 영상 파일도 Git에 포함하지 않음.
* 테스트를 초기 상태에서 다시 실행하려면 기존 DB 파일을 삭제한 뒤 `init_db.py`를 다시 실행함.
* 오탐으로 판단된 이벤트는 상세 이벤트 및 검토 데이터를 유지하지 않고 비식별 집계만 저장함.
* 통계와 교육 추천은 생성 API 실행 이후 조회 가능함.
* 현재 후보 이벤트 서버 측 필터 API는 `main`에 통합되지 않았으며, 프론트엔드 화면 필터링으로 시연 기능을 제공함.
