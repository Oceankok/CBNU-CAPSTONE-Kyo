\# YOLO 안전모 감지 실행 가이드



\## 1. 목적



본 문서는 YOLO 기반 안전모 감지 모델 학습, 이미지 및 영상 추론, 안전모 미착용 후보 이벤트 저장 흐름을 실행하기 위한 절차를 정리한다.



\## 2. 실행 환경



\- Python 가상환경 사용

\- Ultralytics YOLO 모델 사용

\- SQLite 기반 candidate\_event 저장 구조 사용

\- FastAPI 기반 이벤트 조회 API 사용



\## 3. 모델 학습



안전모 감지 모델 학습은 다음 명령어로 실행한다.



```bash

python src/train\_ppe.py

