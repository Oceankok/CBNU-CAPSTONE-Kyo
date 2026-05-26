# Backend Services

백엔드 서비스 계층은 AI 탐지 결과와 DB 저장 구조를 연결하는 역할을 수행한다.

## Candidate Event Media Service

`candidate_event_service.py`는 AI 탐지 결과를 `candidate_event` DB 구조에 맞게 변환하고 저장한다.

주요 기능은 다음과 같다.

- 후보 이벤트 ID 생성
- 이벤트 썸네일 이미지 저장
- 영상 또는 참조 클립 경로 정리
- `candidate_event` DB 저장

## 필요 패키지

`candidate_event_service.py`는 이벤트 썸네일 이미지를 저장하기 위해 OpenCV를 사용한다.  
수동 테스트 스크립트에서는 테스트 이미지를 생성하기 위해 NumPy도 사용한다.

필요 패키지:

```bash
pip install opencv-python numpy
```

또는 `requirements.txt`를 사용하는 경우 아래 항목을 추가한다.

```text
opencv-python
numpy
```

## 수동 테스트 방법

DB를 초기화한 뒤 서비스 테스트 스크립트를 실행한다.

```bash
python backend/db/init_db.py
python backend/services/test_candidate_event_service.py
python backend/db/check_db.py
```

정상 동작 시 다음 내용을 확인할 수 있다.

- `storage/candidate_events/thumbnails` 아래에 테스트 썸네일 이미지 생성
- `candidate_event` 테이블에 새 후보 이벤트 저장
- `thumbnail_path`에 프로젝트 기준 상대 경로 저장
- `video_clip_path`에 참조 영상 경로 저장

## 사용 예시

```python
from backend.services.candidate_event_service import create_no_helmet_candidate_event

saved_event = create_no_helmet_candidate_event(
    camera_id="CAM_001",
    confidence=0.88,
    source_path="test_videos/test_video1.avi",
    frame_image=result.orig_img,
    model_version="helmet_yolov8n",
)

print(saved_event["event_id"])
```

## 저장 경로

AI 탐지 결과로 생성된 후보 이벤트의 썸네일과 영상 참조 파일은 학습 데이터와 분리하기 위해 `storage/candidate_events` 아래에 저장한다.

| 경로 | 설명 |
|---|---|
| `storage/candidate_events/thumbnails` | 후보 이벤트 검토용 썸네일 이미지 |
| `storage/candidate_events/clips` | 후보 이벤트 검토용 영상 클립 또는 참조 영상 |

실제 생성되는 이미지와 영상 파일은 Git에 포함하지 않고, `.gitkeep`만 유지한다.

## 참고 사항

현재 단계에서는 실제 짧은 클립 생성보다, 원본 영상 또는 추론 결과 영상 경로를 `video_clip_path`에 저장하는 구조를 우선 사용한다.

짧은 영상 클립 생성 기능은 이후 `_save_clip()` 함수 등을 추가하여 확장할 수 있다.

---

## Warning Broadcast Service

`warning_broadcast_service.py`는 경고 방송 설정을 기반으로 PPE 미착용 후보 이벤트에 대한 방송 메시지를 선택하고, 1차 단계에서는 터미널 출력 방식으로 방송을 시뮬레이션한다.

주요 기능은 다음과 같다.

- 방송 사용 여부(`enabled`) 확인
- 기본 언어(`default_language`) 기준 메시지 선택
- PPE 유형 및 구역별 메시지 템플릿 선택
- 특정 구역 템플릿이 없을 경우 전체 구역 템플릿 사용
- cooldown 시간 내 동일 상황의 중복 방송 방지
- 방송 실행 결과를 dict와 터미널 로그 형태로 반환

## 방송 실행 흐름

```text
AI 탐지 결과 발생
→ candidate_event 저장
→ 저장된 이벤트의 zone_name 조회
→ 경고 방송 설정 조회
→ 메시지 선택
→ cooldown 확인
→ 터미널 경고 메시지 출력
```

## 터미널 출력 예시

```text
[WARNING BROADCAST]
event_id=EVT_20260526153000_a1b2c3
result=broadcast_printed
ppe_type=helmet
zone_name=프레스 구역
language=ko
message=프레스 구역 작업자는 안전모 착용 상태를 확인해 주세요.
executed_at=2026-05-26 15:30:00
```

cooldown으로 방송이 생략된 경우 다음과 같이 출력된다.

```text
[WARNING BROADCAST SKIPPED]
event_id=EVT_20260526153000_a1b2c3
result=cooldown_active
ppe_type=helmet
zone_name=프레스 구역
language=ko
message=프레스 구역 작업자는 안전모 착용 상태를 확인해 주세요.
cooldown_remaining_sec=30
executed_at=2026-05-26 15:30:01
```

## 수동 테스트 방법

DB 초기화 후 경고 방송 서비스 테스트 스크립트를 실행한다.

```bash
python backend/db/init_db.py
python -m backend.services.test_warning_broadcast_service
```

테스트 항목:

- 후보 이벤트 저장 후 경고 방송 출력 확인
- PPE 유형 및 구역별 메시지 선택 확인
- 기본 언어 변경 시 영어 메시지 선택 확인
- cooldown 시간 내 재방송 생략 확인
- 방송 비활성화 시 실행 생략 확인

정상 동작 시 다음 메시지를 확인할 수 있다.

```text
[OK] warning broadcast service test passed.
```

## 참고 사항

현재 단계에서는 실제 음성 출력을 수행하지 않고, 터미널 출력으로 방송 실행 흐름을 검증한다.

후속 단계에서는 `execute_warning_broadcast()`가 선택한 `message`를 TTS 모듈에 전달하여 실제 음성 출력으로 확장할 수 있다.

현재 cooldown 상태는 실행 중인 서버 또는 프로세스 메모리에서 관리된다. 서버를 재시작하면 cooldown 기록은 초기화된다.