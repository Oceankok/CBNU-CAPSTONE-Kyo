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