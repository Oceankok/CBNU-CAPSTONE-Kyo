# SCRFD 비교 모드 출처 및 사용 범위

이 프로젝트의 SCRFD 모드는 비상업적 대학 캡스톤에서 얼굴 비식별화 성능을 비교하기 위한 선택 기능이다. 개인 식별이나 얼굴 임베딩 추출은 하지 않는다.

- 알고리즘/ONNX 입출력 설명: https://github.com/deepinsight/insightface/tree/master/detection/scrfd
- 공식 사용 조건: https://github.com/deepinsight/insightface#license
- 논문: Guo et al., *Sample and Computation Redistribution for Efficient Face Detection*, ICLR 2022, https://arxiv.org/abs/2105.04714

InsightFace 소스 코드는 MIT 조건이지만, 제공하는 학습 데이터와 사전 학습 모델은 **비상업적 연구 목적만 허용**한다. 코드 라이선스가 모델 가중치의 사용 제한을 없애지는 않는다. 교과 프로젝트의 개발·실험·평가·발표를 비상업적 학술 연구 범위로 해석하여 사용하며, 캡스톤을 명시적으로 승인받았다는 뜻은 아니다. 사업장 운영, 납품, 제품화 등으로 범위가 바뀌면 사용 조건을 다시 확인한다.

가중치는 Git에 포함하지 않는다. 공식 SCRFD 자료의 모델 링크에서 10G ONNX를 별도로 받아 로컬에서 등록한다. `models/privacy/`의 모델·체크섬 기록·출처 메모는 개발 산출물로 제외된다. 로컬 체크섬은 등록 이후 파일 변조/손상을 감지하며, 제공자가 게시한 체크섬으로 다운로드 출처를 검증한 것은 아니다.

얼굴 모델만으로 뒷머리 탐지를 보장하지 않는다. 현재 모자이크 여백이 안전모 일부를 가릴 수도 있다. 처리 완료 상태는 알고리즘 실행 완료를 뜻하며, 모든 얼굴의 비식별화를 검증했다는 뜻은 아니다.
