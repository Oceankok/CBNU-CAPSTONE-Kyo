# Backend Services

이 폴더는 PPE 분석 시스템의 서비스 계층 코드를 포함함.

서비스 계층은 AI 탐지 결과를 후보 이벤트 데이터로 변환하여 저장하고, 저장된 경고 방송 설정을 기준으로 현장 경고 메시지 출력 및 선택적 TTS 음성 방송을 수행함.

현재 직접 연결된 이벤트 유형은 안전모(`helmet`) 미착용 후보 이벤트이며, 실제 AI 탐지 코드에서는 DB 구조를 직접 다루지 않고 서비스 함수를 호출하는 방식으로 연동함.

---

## 파일 구성

| 파일                                  | 설명                                                         |
| ----------------------------------- | ---------------------------------------------------------- |
| `candidate_event_service.py`        | AI 탐지 결과를 후보 이벤트로 변환하고, 썸네일·영상 참조 경로 저장 및 경고 방송 실행을 연결함    |
| `warning_broadcast_service.py`      | 방송 설정 조회, 메시지 선택, cooldown 판단, 터미널 로그 출력 및 선택적 TTS 실행을 처리함 |
| `tts_service.py`                    | 선택된 경고 메시지를 시스템 음성을 이용해 실제 음성으로 출력함                        |
| `test_candidate_event_service.py`   | 후보 이벤트 저장 및 미디어 경로 생성 확인용 수동 테스트                           |
| `test_warning_broadcast_service.py` | 후보 이벤트-경고 방송-TTS 연결, cooldown, 방송 OFF, fallback 확인용 수동 테스트 |
| `test_tts_service.py`               | 시스템 음성 목록 및 한국어·영어 TTS 단독 출력 확인용 수동 테스트                    |

---

## 필요 패키지

후보 이벤트 썸네일 저장과 TTS 음성 출력을 위해 아래 패키지를 사용함.

```bash
pip install opencv-python numpy pyttsx3
```

또는 `requirements.txt`에 아래 항목을 포함함.

```text
opencv-python
numpy
pyttsx3
```

| 패키지             | 사용 목적                  |
| --------------- | ---------------------- |
| `opencv-python` | 후보 이벤트 썸네일 이미지 저장      |
| `numpy`         | 수동 테스트용 프레임 이미지 데이터 생성 |
| `pyttsx3`       | 로컬 시스템 음성을 이용한 TTS 출력  |

---

# 전체 처리 흐름

현재 서비스 계층은 아래 흐름을 지원함.

```text
AI 안전모 미착용 탐지
→ create_no_helmet_candidate_event() 호출
→ 후보 이벤트 ID 생성
→ 탐지 프레임 썸네일 저장
→ 원본 영상 또는 참조 영상 경로 정리
→ candidate_event DB 저장
→ 이벤트 발생 구역 조회
→ 경고 방송 설정 조회
→ PPE 유형·구역·언어에 맞는 메시지 선택
→ cooldown 확인
→ 터미널 경고 로그 출력
→ enable_tts=True인 경우 TTS 음성 출력
```

후보 이벤트 저장은 관리자 확정 이전의 `pending` 상태로 이루어지며, 경고 방송은 사고 예방을 위한 즉시 알림이므로 관리자 검토 이전에 실행됨.

---

# Candidate Event Service

## 개요

`candidate_event_service.py`는 AI 탐지 결과를 `candidate_event` DB 구조에 맞게 변환하고 저장하는 연결 계층임.

AI 담당 코드는 후보 이벤트 DB 필드를 직접 구성하거나 `insert_candidate_event()`를 직접 호출하지 않고, 이 서비스 함수를 호출하여 탐지 결과를 전달하는 방식으로 연동함.

## 현재 제공 함수

```python
from backend.services.candidate_event_service import create_no_helmet_candidate_event
```

현재 실제 이벤트 생성 흐름과 직접 연결된 함수는 안전모 미착용 후보 이벤트를 생성하는 `create_no_helmet_candidate_event()`임.

## 주요 기능

* 후보 이벤트 ID 생성
* 탐지 프레임 기반 썸네일 이미지 저장
* 원본 영상 또는 참조 영상 경로 정리
* `candidate_event` DB 저장
* 저장된 이벤트의 구역 정보 조회
* 후보 이벤트 저장 직후 경고 방송 실행 연결
* `enable_tts` 값에 따른 실제 음성 출력 여부 제어

---

## AI 코드 연동 예시

```python
from backend.services.candidate_event_service import create_no_helmet_candidate_event

saved_event = create_no_helmet_candidate_event(
    camera_id="CAM_001",
    confidence=max_no_helmet_confidence,
    source_path=source_name,
    frame_image=result.orig_img,
    model_version="helmet_yolov8n",
    duration_sec=duration_sec,
    frame_sample_count=frame_sample_count,
    tracking_id=tracking_id,
    enable_tts=True,
)

print(saved_event["event_id"])
print(saved_event["broadcast"])
```

## 전달값 기준

| 인자                   | 설명                          |
| -------------------- | --------------------------- |
| `camera_id`          | 탐지 영상에 대응하는 카메라 ID          |
| `confidence`         | 안전모 미착용 탐지 신뢰도              |
| `source_path`        | 원본 영상, 추론 결과 영상 또는 참조 파일 경로 |
| `frame_image`        | 검토용 썸네일로 저장할 탐지 프레임 이미지     |
| `model_version`      | 탐지에 사용한 모델 버전               |
| `timestamp_start`    | 이벤트 시작 시각                   |
| `timestamp_end`      | 이벤트 종료 시각                   |
| `duration_sec`       | 미착용 상태 지속 시간                |
| `frame_sample_count` | 이벤트 판단에 사용한 프레임 수           |
| `tracking_id`        | 탐지 객체 추적 ID                 |
| `enable_tts`         | 실제 음성 경고 방송 실행 여부           |

---

## TTS 실행 여부 제어

`enable_tts=True`인 경우 후보 이벤트 저장 후 터미널 로그와 실제 음성 방송을 모두 수행함.

```python
saved_event = create_no_helmet_candidate_event(
    camera_id="CAM_001",
    confidence=0.88,
    enable_tts=True,
)
```

`enable_tts=False`인 경우 후보 이벤트 저장과 터미널 경고 로그 출력은 수행하지만, 실제 TTS 음성 출력은 수행하지 않음.

```python
saved_event = create_no_helmet_candidate_event(
    camera_id="CAM_001",
    confidence=0.88,
    enable_tts=False,
)
```

이 값은 테스트, 개발 환경, 실제 시연 상황에 따라 음성 출력 여부를 제어하기 위해 사용함.

---

## 저장 경로

후보 이벤트 관련 미디어 파일은 학습 데이터와 분리하여 `storage/candidate_events` 아래에 저장함.

| 경로                                    | 설명                          |
| ------------------------------------- | --------------------------- |
| `storage/candidate_events/thumbnails` | 후보 이벤트 검토용 썸네일 이미지          |
| `storage/candidate_events/clips`      | 후보 이벤트 영상 클립 또는 참조 영상 저장 경로 |

썸네일 이미지가 생성되는 경우 DB의 `thumbnail_path`에는 프로젝트 루트 기준 상대 경로를 저장함.

```text
storage/candidate_events/thumbnails/EVT_20260519160059_b95d63.jpg
```

현재 단계에서는 이벤트 발생 전후 구간을 잘라 별도 영상 클립 파일을 생성하기보다, 원본 영상 또는 추론 결과 영상 경로를 `video_clip_path`에 저장하는 구조를 우선 사용함.

생성되는 이미지와 영상 파일은 로컬 실행 산출물이므로 Git에 포함하지 않으며, 폴더 구조 유지를 위한 `.gitkeep`만 관리함.

---

## 후보 이벤트 서비스 수동 테스트

프로젝트 루트에서 아래 순서로 실행함.

```bash
python backend/db/init_db.py
python -m backend.services.test_candidate_event_service
python backend/db/check_db.py
```

정상 동작 시 아래 항목을 확인할 수 있음.

* 후보 이벤트 ID 생성
* `candidate_event` 테이블에 신규 이벤트 저장
* 썸네일 이미지 생성 및 상대 경로 저장
* 영상 참조 경로 저장
* 저장된 이벤트 정보 조회 가능

---

# Warning Broadcast Service

## 개요

`warning_broadcast_service.py`는 DB에 저장된 경고 방송 설정을 조회하고, PPE 미착용 후보 이벤트에 맞는 메시지를 선택하여 터미널 로그와 선택적 TTS 음성 방송을 실행하는 서비스임.

경고 방송은 관리자 검토 결과와 무관하게, 후보 이벤트가 발생한 시점에 즉시 시정 알림을 제공하기 위한 흐름으로 동작함.

## 주요 기능

* 방송 사용 여부(`enabled`) 확인
* 기본 방송 언어(`default_language`) 적용
* PPE 유형·구역·언어 기준 메시지 템플릿 선택
* 특정 구역 메시지가 없는 경우 전체 구역 메시지 fallback
* cooldown 시간 내 동일 상황 반복 방송 차단
* 터미널 로그 출력
* `enable_tts=True`인 경우 실제 TTS 음성 출력
* TTS 실패 시에도 터미널 로그 fallback 유지
* 방송 실행 결과를 dict 형태로 반환

---

## 방송 설정 연동 기준

경고 방송 서비스는 방송 설정 API를 통해 저장된 아래 값을 사용함.

| 설정값                | 설명                       |
| ------------------ | ------------------------ |
| `enabled`          | 전체 경고 방송 사용 여부           |
| `default_language` | 기본 방송 언어                 |
| `cooldown_sec`     | 동일 상황에 대한 반복 방송 제한 시간    |
| `templates`        | PPE 유형·구역·언어별 메시지 템플릿 목록 |

메시지 템플릿은 아래 값으로 구성됨.

| 항목          | 설명                          |
| ----------- | --------------------------- |
| `ppe_type`  | PPE 유형. 예: `helmet`, `vest` |
| `zone_name` | 특정 구역명. 빈 문자열이면 전체 구역 대상    |
| `language`  | 방송 언어. 예: `ko`, `en`        |
| `message`   | 실제 경고 방송 문구                 |

---

## 메시지 선택 우선순위

동일한 PPE 유형과 언어에 대해 특정 구역용 메시지와 전체 구역용 메시지가 함께 존재할 경우, 특정 구역용 메시지를 우선 사용함.

```text
1순위: PPE 유형 + 특정 구역 + 언어가 모두 일치하는 메시지
2순위: PPE 유형 + 전체 구역("") + 언어가 일치하는 메시지
```

예시:

```text
helmet / 프레스 구역 / ko / "프레스 구역 작업자는 안전모 착용 상태를 확인해 주세요."
helmet / ""          / ko / "해당 작업 구역의 작업자는 안전모 착용 상태를 확인해 주세요."
```

프레스 구역에서 안전모 미착용 후보 이벤트가 발생하면 특정 구역용 메시지를 사용하고, 그 외 구역에서는 전체 구역용 메시지를 사용할 수 있음.

---

## 방송 실행 예시

```python
from backend.services.warning_broadcast_service import execute_warning_broadcast

result = execute_warning_broadcast(
    event_id="EVT_TEST",
    ppe_type="helmet",
    zone_name="프레스 구역",
    enable_tts=True,
)

print(result)
```

---

## 정상 방송 출력 예시

```text
[WARNING BROADCAST]
event_id=EVT_20260526203656_6f8745
result=broadcast_printed
ppe_type=helmet
zone_name=프레스 구역
language=ko
message=프레스 구역 작업자는 안전모 착용 상태를 확인해 주세요.
executed_at=2026-05-26 20:36:56
tts_result=tts_completed
tts_voice=Microsoft Heami Desktop - Korean
```

---

## cooldown 적용 예시

동일 상황이 설정된 cooldown 시간 내 반복되는 경우, 추가 음성 방송을 실행하지 않음.

```text
[WARNING BROADCAST SKIPPED]
event_id=EVT_20260526203656_6f8745
result=cooldown_active
ppe_type=helmet
zone_name=프레스 구역
language=ko
message=프레스 구역 작업자는 안전모 착용 상태를 확인해 주세요.
cooldown_remaining_sec=25
executed_at=2026-05-26 20:37:01
```

---

## 방송 비활성화 예시

방송 설정의 `enabled` 값이 `false`인 경우 메시지 선택 및 TTS 실행을 수행하지 않음.

```text
[WARNING BROADCAST SKIPPED]
event_id=TEST_DISABLED_EVENT
result=broadcast_disabled
ppe_type=helmet
zone_name=프레스 구역
language=ko
executed_at=2026-05-26 20:37:01
```

---

## TTS 실패 fallback 예시

TTS 모듈 로드 또는 음성 엔진 실행 중 오류가 발생하더라도, 후보 이벤트 저장 및 터미널 경고 로그 출력 흐름은 중단되지 않음.

```text
[WARNING BROADCAST]
event_id=TEST_TTS_FAILURE_EVENT
result=broadcast_printed
ppe_type=helmet
zone_name=프레스 구역
language=ko
message=프레스 구역 작업자는 안전모 착용 상태를 확인해 주세요.
executed_at=2026-05-26 20:47:52
tts_result=tts_error
```

이 경우 실제 음성 출력에는 실패했지만, 경고 메시지와 실행 결과는 터미널 로그 및 반환 데이터에서 확인할 수 있음.

---

## cooldown 처리 기준

현재 cooldown 상태는 실행 중인 Python 프로세스 메모리에서 관리함.

동일한 아래 조합의 방송 요청이 cooldown 시간 내 반복되면 추가 방송을 생략함.

```text
ppe_type + zone_name + language
```

예시:

```text
helmet + 프레스 구역 + ko
```

cooldown 기록은 아래 상황에서 초기화됨.

* 서버 또는 테스트 프로세스 재시작
* `reset_broadcast_cooldown()` 호출

현재 단계에서는 시연과 로컬 실행을 위한 메모리 기반 관리 방식을 사용함.

---

## 경고 방송 서비스 수동 테스트

프로젝트 루트에서 아래 순서로 실행함.

```bash
python backend/db/init_db.py
python -m backend.services.test_warning_broadcast_service
```

### 테스트 항목

* 후보 이벤트 저장 후 구역별 한국어 메시지 선택 확인
* 후보 이벤트 저장 후 한국어 TTS 음성 출력 확인
* 동일 상황 재발생 시 cooldown 기반 반복 방송 차단 확인
* 기본 언어 변경 시 영어 메시지 템플릿 선택 확인
* 방송 비활성화 시 방송 실행 생략 확인
* TTS 예외 발생 시 터미널 로그 fallback 유지 확인
* 후보 이벤트 생성 시 `enable_tts=False`가 적용되어 실제 음성 출력이 생략되는지 확인

### 확인 사항

본 테스트는 실제 한국어 TTS 음성을 출력하는 수동 테스트임.

Windows 환경에서는 설치된 시스템 음성을 사용하며, 확인 환경에서는 아래 한국어 음성 출력이 정상 동작함.

```text
Microsoft Heami Desktop - Korean
```

정상 동작 시 마지막에 아래 메시지를 확인할 수 있음.

```text
[OK] warning broadcast service test passed.
```

---

# TTS Service

## 개요

`tts_service.py`는 경고 방송 서비스에서 선택한 메시지를 실제 음성으로 출력하는 역할을 수행함.

현재 구현에서는 `pyttsx3`를 사용하며, Windows 환경에서는 시스템에 설치된 음성 엔진을 사용함.

## 주요 기능

* 시스템에서 사용 가능한 음성 목록 조회
* 요청 언어에 맞는 시스템 음성 선택
* 한국어·영어 경고 메시지 출력
* 음성 출력 성공 여부 반환
* 음성 미탐색 또는 엔진 실행 오류 결과 반환

---

## TTS 실행 결과

| `reason`          | 의미                            |
| ----------------- | ----------------------------- |
| `tts_completed`   | 요청 메시지 음성 출력 완료               |
| `voice_not_found` | 요청 언어에 맞는 시스템 음성을 찾지 못함       |
| `tts_error`       | TTS 모듈 로드 또는 음성 엔진 실행 중 오류 발생 |

TTS 실행에 실패하더라도 경고 방송 서비스에서는 실패 결과를 기록하고, 기존 터미널 로그 출력 결과를 유지함.

---

## TTS 단독 실행 예시

```python
from backend.services.tts_service import speak_message

result = speak_message(
    message="프레스 구역 작업자는 안전모 착용 상태를 확인해 주세요.",
    language="ko",
)

print(result)
```

---

## TTS 단독 수동 테스트

프로젝트 루트에서 아래 명령어를 실행함.

```bash
python -m backend.services.test_tts_service
```

### 테스트 항목

* 시스템에 설치된 음성 목록 출력
* 한국어 경고 메시지 음성 출력
* 영어 경고 메시지 음성 출력

정상 동작 시 실제 안내 음성이 출력되고, 마지막에 아래 메시지를 확인할 수 있음.

```text
[OK] TTS service test passed.
```

## 실행 환경 관련 참고 사항

* TTS 음성 품질과 사용 가능한 언어는 실행 환경에 설치된 시스템 음성에 따라 달라질 수 있음.
* Windows 환경에서 한국어 음성이 설치되어 있지 않으면 `voice_not_found` 또는 `tts_error` 결과가 반환될 수 있음.
* TTS 실행 오류는 후보 이벤트 저장 자체를 실패 처리하지 않으며, 방송 실행 결과의 `tts` 항목을 통해 확인함.

---

# AI 담당자 연계 방법

## 연계 대상 함수

AI 탐지 코드에서는 안전모 미착용 후보가 확정되었을 때 아래 함수를 호출함.

```python
from backend.services.candidate_event_service import create_no_helmet_candidate_event
```

## 연계 시 주의 사항

AI 코드에서 기존에 아래와 같이 DB 저장을 직접 수행하고 있다면, 서비스 호출 방식으로 교체해야 함.

```python
from backend.db.event_repository import insert_candidate_event
```

```python
event = {
    # 직접 구성한 후보 이벤트 데이터
}

insert_candidate_event(event)
```

위 직접 저장 로직을 유지한 상태에서 `create_no_helmet_candidate_event()`까지 추가 호출하면 동일 탐지 결과가 중복 저장될 수 있음.

## 기대 연계 흐름

```text
YOLO 안전모 미착용 탐지
→ confidence 및 탐지 프레임 추출
→ create_no_helmet_candidate_event() 호출
→ candidate_event 저장
→ 썸네일 이미지 저장
→ 방송 설정 조회
→ 터미널 경고 로그 출력
→ 필요 시 TTS 경고 방송 실행
→ 프론트엔드 검토 화면에서 이벤트 확인
```

---

# 전체 백엔드 통합 검증

DB, 재검토 API, 후보 이벤트 서비스 기능을 한 번에 확인하려면 아래 runner를 실행함.

## 기본 검증

실제 음성 출력 없이 반복 실행 가능한 검증 흐름을 수행함.

```bash
python -m backend.run_verification
```

### 기본 검증 범위

* DB 초기화
* 후보 이벤트 및 검토 repository 동작 확인
* 재검토 API 성공·거부·오탐 처리 흐름 확인
* 후보 이벤트 저장 및 썸네일 생성 확인

## 음성 출력 포함 검증

TTS 및 경고 방송까지 포함하여 확인하려면 `--include-audio` 옵션을 사용함.

```bash
python -m backend.run_verification --include-audio
```

### 추가 검증 범위

* 한국어·영어 TTS 음성 출력
* 후보 이벤트 저장 후 경고 방송 출력
* cooldown 기반 중복 방송 차단
* 방송 OFF 설정 적용
* TTS 실패 fallback 처리
* `enable_tts=False` 적용 시 실제 음성 출력 생략

음성 출력 포함 검증은 실행 환경에 설치된 시스템 음성을 사용하므로, 시연 전 실제 사용 환경에서 최종 확인하는 용도로 사용함.


## 확인 항목

* 후보 이벤트 저장 및 이벤트 ID 생성
* 썸네일 이미지 및 미디어 참조 경로 저장
* TTS 단독 한국어·영어 출력
* 후보 이벤트 발생 후 방송 설정 기반 메시지 선택
* 실제 한국어 TTS 경고 방송 출력
* cooldown 기반 반복 방송 차단
* 방송 OFF 설정 반영
* TTS 실패 fallback 처리
* `enable_tts=False` 적용 시 음성 출력 생략
* 테스트 종료 후 생성된 테스트 후보 이벤트 정리

---

# 현재 구현 범위 및 후속 확장

## 현재 구현 범위

* 안전모 미착용 후보 이벤트 생성 및 DB 저장
* 후보 이벤트 검토용 썸네일 이미지 저장
* 원본 영상 또는 참조 영상 경로 저장
* 후보 이벤트 저장 후 경고 방송 실행 서비스 연결
* 경고 방송 설정값 조회 및 적용
* PPE 유형·구역·언어 기준 메시지 선택
* cooldown 기반 중복 방송 방지
* 터미널 기반 경고 로그 출력
* 한국어 TTS 경고 방송 출력 확인
* 영어 TTS 단독 출력 확인
* TTS 실패 시 터미널 로그 fallback 처리
* `enable_tts` 값에 따른 실제 음성 출력 여부 제어

## 후속 확장 대상

* 실제 AI 탐지 코드에서 후보 이벤트 저장 서비스 호출 연결
* 안전조끼(`vest`) 미착용 이벤트 생성 흐름 직접 연결
* 이벤트 발생 구간 기준 실제 짧은 영상 클립 생성
* 방송 실행 이력 DB 저장 및 관리자 조회
* 서버 재시작 이후에도 유지되는 cooldown 관리
* 현장 방송 장비 또는 별도 알림 장치 연동

---

## 주의 사항

* 현재 실제 후보 이벤트 생성 서비스와 직접 연결된 PPE 유형은 안전모(`helmet`) 미착용임.
* 안전조끼(`vest`)는 메시지 템플릿 선택 구조와 TTS 출력 테스트 범위에서 사용할 수 있으나, AI 탐지 결과 기반 이벤트 생성 흐름은 후속 확장 대상임.
* 현재 영상 경로 저장은 실제 클립 생성보다 원본 영상 또는 참조 영상 경로 보존을 우선함.
* 생성된 썸네일 및 영상 파일은 로컬 실행 산출물이므로 Git에 포함하지 않음.
* 실제 AI 코드 연동 전에는 수동 테스트 스크립트로 저장·방송·TTS 동작을 검증함.
