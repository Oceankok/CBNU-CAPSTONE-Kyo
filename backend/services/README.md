# Backend Services

백엔드 서비스 계층은 AI 탐지 결과를 후보 이벤트 데이터로 변환하고, 저장된 경고 방송 설정에 따라 경고 메시지 출력 및 TTS 음성 방송을 수행하는 역할을 담당함.

---

## 파일 구성

| 파일 | 설명 |
|---|---|
| `candidate_event_service.py` | AI 탐지 결과를 후보 이벤트로 변환하고 DB 저장 및 경고 방송 실행을 연결함 |
| `warning_broadcast_service.py` | 방송 설정 조회, 메시지 선택, cooldown 적용, 터미널 로그 및 TTS 실행을 처리함 |
| `tts_service.py` | 선택된 경고 메시지를 실제 음성으로 출력함 |
| `test_candidate_event_service.py` | 후보 이벤트 저장 및 썸네일 경로 생성 확인용 수동 테스트 |
| `test_warning_broadcast_service.py` | 후보 이벤트 저장, 메시지 선택, cooldown, 방송 OFF, TTS 실행 확인용 수동 테스트 |
| `test_tts_service.py` | 시스템 음성 목록 및 한국어·영어 TTS 단독 출력 확인용 수동 테스트 |

---

## 필요 패키지

후보 이벤트 썸네일 저장과 TTS 음성 출력을 위해 아래 패키지를 사용함.

```bash
pip install opencv-python numpy pyttsx3
```

또는 `requirements.txt`를 사용하는 경우 아래 항목을 추가함.

```text
opencv-python
numpy
pyttsx3
```

| 패키지 | 사용 목적 |
|---|---|
| `opencv-python` | 후보 이벤트 썸네일 이미지 저장 |
| `numpy` | 수동 테스트용 이미지 데이터 생성 |
| `pyttsx3` | 경고 메시지 TTS 음성 출력 |

---

# Candidate Event Media Service

## 개요

`candidate_event_service.py`는 AI 탐지 결과를 `candidate_event` DB 구조에 맞게 변환하고 저장하는 서비스임.

현재는 안전모 미착용 후보 이벤트 생성을 기준으로 구현되어 있으며, 후보 이벤트 저장 이후 경고 방송 실행 서비스까지 연결함.

## 주요 기능

- 후보 이벤트 ID 생성
- 이벤트 썸네일 이미지 저장
- 영상 또는 참조 클립 경로 정리
- `candidate_event` DB 저장
- 저장된 이벤트의 구역 정보 조회
- 후보 이벤트 저장 후 경고 방송 실행 연결

## 처리 흐름

```text
AI 안전모 미착용 탐지
→ 후보 이벤트 ID 생성
→ 썸네일 이미지 저장
→ 영상 참조 경로 정리
→ candidate_event DB 저장
→ 저장된 이벤트의 zone_name 조회
→ 경고 방송 실행 서비스 호출
```

## 사용 예시

```python
from backend.services.candidate_event_service import create_no_helmet_candidate_event

saved_event = create_no_helmet_candidate_event(
    camera_id="CAM_001",
    confidence=0.88,
    source_path="test_videos/test_video1.avi",
    frame_image=result.orig_img,
    model_version="helmet_yolov8n",
    enable_tts=True,
)

print(saved_event["event_id"])
print(saved_event["broadcast"])
```

`enable_tts` 값에 따라 실제 음성 출력 여부를 제어할 수 있음.

```python
# 실제 음성 방송까지 수행
create_no_helmet_candidate_event(
    camera_id="CAM_001",
    confidence=0.88,
    enable_tts=True,
)

# 터미널 로그만 출력
create_no_helmet_candidate_event(
    camera_id="CAM_001",
    confidence=0.88,
    enable_tts=False,
)
```

## 저장 경로

AI 탐지 결과로 생성된 후보 이벤트의 썸네일과 영상 참조 파일은 학습 데이터와 분리하기 위해 `storage/candidate_events` 아래에 저장함.

| 경로 | 설명 |
|---|---|
| `storage/candidate_events/thumbnails` | 후보 이벤트 검토용 썸네일 이미지 |
| `storage/candidate_events/clips` | 후보 이벤트 검토용 영상 클립 또는 참조 영상 |

실제 생성되는 이미지와 영상 파일은 Git에 포함하지 않고, `.gitkeep`만 유지함.

## 수동 테스트 방법

프로젝트 루트에서 DB 초기화 후 후보 이벤트 저장 테스트를 실행함.

```bash
python backend/db/init_db.py
python -m backend.services.test_candidate_event_service
python backend/db/check_db.py
```

정상 동작 시 다음 내용을 확인할 수 있음.

- `storage/candidate_events/thumbnails` 아래에 테스트 썸네일 이미지 생성
- `candidate_event` 테이블에 새 후보 이벤트 저장
- `thumbnail_path`에 프로젝트 기준 상대 경로 저장
- `video_clip_path`에 참조 영상 경로 저장

## 참고 사항

현재 단계에서는 실제 짧은 클립 파일을 새로 생성하기보다, 원본 영상 또는 추론 결과 영상 경로를 `video_clip_path`에 저장하는 구조를 우선 사용함.

짧은 영상 클립 생성 기능은 이후 `_save_clip()` 함수 등을 추가하여 확장 가능함.

---

# Warning Broadcast Service

## 개요

`warning_broadcast_service.py`는 저장된 경고 방송 설정을 조회하고, PPE 미착용 후보 이벤트에 맞는 경고 메시지를 선택하여 터미널 로그와 TTS 음성 방송을 실행하는 서비스임.

## 주요 기능

- 방송 사용 여부(`enabled`) 확인
- 기본 언어(`default_language`) 기준 메시지 선택
- PPE 유형 및 구역별 메시지 템플릿 선택
- 특정 구역 템플릿이 없을 경우 전체 구역 템플릿 적용
- cooldown 시간 내 동일 상황의 중복 방송 방지
- 터미널 로그 출력
- 선택적으로 TTS 음성 출력
- 방송 실행 결과를 dict 형태로 반환

## 방송 설정 연동 기준

경고 방송 실행 서비스는 방송 설정 API를 통해 저장된 다음 값을 사용함.

| 설정값 | 사용 목적 |
|---|---|
| `enabled` | 경고 방송 전체 사용 여부 확인 |
| `default_language` | 기본 방송 언어 선택 |
| `cooldown_sec` | 동일 상황의 반복 방송 제한 시간 |
| `templates` | PPE 유형·구역·언어별 경고 메시지 선택 |

메시지 템플릿은 다음 기준으로 관리됨.

```text
ppe_type
zone_name
language
message
```

## 메시지 선택 우선순위

동일한 PPE 유형과 언어에 대해 특정 구역용 템플릿과 전체 구역용 템플릿이 함께 존재할 경우, 특정 구역용 메시지를 우선 사용함.

```text
1순위: PPE 유형 + 특정 구역 + 언어가 모두 일치하는 템플릿
2순위: PPE 유형 + 전체 구역("") + 언어가 일치하는 템플릿
```

예시:

```text
helmet / 프레스 구역 / ko / "프레스 구역 작업자는 안전모 착용 상태를 확인해 주세요."
helmet / ""          / ko / "해당 작업 구역의 작업자는 안전모 착용 상태를 확인해 주세요."
```

프레스 구역에서 안전모 미착용 이벤트가 발생하면 특정 구역 메시지를 사용하고, 다른 구역에서 발생하면 전체 구역 메시지를 사용함.

## 방송 실행 흐름

```text
후보 이벤트 저장
→ 저장된 이벤트의 zone_name 조회
→ 경고 방송 설정 조회
→ enabled 확인
→ PPE 유형·구역·언어에 맞는 메시지 선택
→ cooldown 확인
→ 터미널 로그 출력
→ enable_tts=True인 경우 TTS 음성 출력
```

## 터미널 출력 예시

정상적으로 방송이 실행된 경우:

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

cooldown으로 반복 방송이 생략된 경우:

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

방송 설정이 비활성화된 경우:

```text
[WARNING BROADCAST SKIPPED]
event_id=TEST_DISABLED_EVENT
result=broadcast_disabled
ppe_type=helmet
zone_name=프레스 구역
language=ko
executed_at=2026-05-26 20:37:01
```

## cooldown 처리 기준

현재 cooldown은 실행 중인 프로세스 메모리에서 관리함.

동일한 아래 조건의 방송이 cooldown 시간 내 다시 요청되면 음성 방송을 생략함.

```text
ppe_type + zone_name + language
```

예시:

```text
helmet + 프레스 구역 + ko
```

cooldown 상태는 프로세스를 종료하거나 `reset_broadcast_cooldown()`을 호출하면 초기화됨.

## 수동 테스트 방법

프로젝트 루트에서 DB 초기화 후 경고 방송 서비스 테스트를 실행함.

```bash
python backend/db/init_db.py
python -m backend.services.test_warning_broadcast_service
```

테스트 항목:

- 후보 이벤트 저장 후 구역별 한국어 메시지 선택 확인
- 후보 이벤트 저장 후 한국어 TTS 음성 출력 확인
- 동일 상황 재발생 시 cooldown 기반 반복 방송 차단 확인
- 기본 언어 변경 시 영어 메시지 템플릿 선택 확인
- 방송 비활성화 시 방송 실행 생략 확인

주의 사항:

- 본 테스트는 실제 한국어 TTS 음성을 출력하는 수동 테스트임
- Windows 환경에서는 설치된 시스템 음성을 사용함
- 확인 환경에서는 `Microsoft Heami Desktop - Korean` 음성 출력이 정상 동작함

정상 동작 시 다음 메시지를 확인할 수 있음.

```text
tts_result=tts_completed
tts_voice=Microsoft Heami Desktop - Korean
[OK] warning broadcast service test passed.
```

---

# TTS Service

## 개요

`tts_service.py`는 경고 방송 서비스에서 선택한 메시지를 실제 음성으로 출력하는 역할을 수행함.

현재 구현에서는 `pyttsx3`를 사용하며, Windows 환경에서는 시스템에 설치된 음성 엔진을 사용함.

## 주요 기능

- 시스템에서 사용 가능한 음성 목록 조회
- 요청 언어에 맞는 시스템 음성 선택
- 한국어·영어 경고 메시지 음성 출력
- TTS 실행 결과 반환

## 실행 방식

`execute_warning_broadcast()` 호출 시 `enable_tts=True`를 전달하면, 터미널 로그 출력 이후 실제 음성 방송을 수행함.

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

방송이 비활성화되어 있거나 cooldown으로 생략되는 경우에는 TTS도 실행되지 않음.

## TTS 단독 수동 테스트

프로젝트 루트에서 아래 명령어를 실행함.

```bash
python -m backend.services.test_tts_service
```

테스트 항목:

- 시스템에 설치된 음성 목록 출력
- 한국어 경고 메시지 음성 출력
- 영어 경고 메시지 음성 출력

정상 동작 시 한국어 및 영어 안내 음성이 실제로 출력되고, 마지막에 아래 메시지를 확인할 수 있음.

```text
[OK] TTS service test passed.
```

## 참고 사항

TTS 음성 품질과 사용 가능한 언어는 실행 환경에 설치된 시스템 음성에 따라 달라질 수 있음.

Windows에서 한국어 음성이 설치되어 있지 않은 경우, 한국어 메시지 출력이 실패하거나 사용할 수 있는 음성을 찾지 못할 수 있음.

---

# 전체 수동 테스트 순서

프로젝트 루트에서 아래 순서로 실행함.

```bash
python backend/db/init_db.py
python -m backend.services.test_candidate_event_service
python -m backend.services.test_tts_service
python -m backend.services.test_warning_broadcast_service
python backend/db/check_db.py
```

확인 항목:

- 후보 이벤트 저장 및 썸네일 경로 생성
- TTS 단독 한국어·영어 음성 출력
- 후보 이벤트 발생 후 방송 설정 기반 메시지 선택
- 실제 한국어 TTS 경고 방송 출력
- cooldown 기반 반복 방송 차단
- 방송 OFF 설정 적용

---

# 현재 구현 범위 및 후속 확장

## 현재 구현 완료 범위

- 안전모 미착용 후보 이벤트 생성 및 DB 저장
- 후보 이벤트 썸네일 이미지 저장
- 미디어 참조 경로 정리
- 경고 방송 설정값 조회 및 적용
- PPE 유형·구역·언어별 메시지 선택
- cooldown 기반 중복 방송 방지
- 터미널 로그 출력
- 한국어 TTS 경고 방송 출력 확인
- 영어 TTS 단독 출력 확인

## 후속 확장 대상

- 안전조끼 미착용 이벤트 생성 흐름과 방송 실행 직접 연결
- 실제 짧은 영상 클립 생성 및 저장
- 방송 실행 이력 DB 저장
- 서버 재시작 이후에도 유지되는 cooldown 관리
- AI 탐지 코드에서 후보 이벤트 저장 서비스 호출 연결