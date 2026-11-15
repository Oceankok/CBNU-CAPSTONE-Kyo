# 연속 증거 영상의 출력 프레임 경계 계약

기준은 배포된 `f51d47f2c8bcb09ed0fa34642e198f5bea336da9`다. 실제 실행의 일곱 CAM02 capture report에 기록된 `end boundary must align to output frame grid`를 조사했다. 실제 원본 영상은 다시 다운로드하거나 재생하지 않았다. 소스 시각과 기존 보고서는 수정하지 않았다.

## 원인과 실제 값

`MultiCameraCapture.discover()`는 출력 원점을 위반 시작 시각에서 2초 뺀 값으로 정한다. `end()`는 마지막 양성 관찰 시각에 2초를 더한다. 서로 다른 청크의 UTC 위상이 달라지면 두 끝의 차이가 정확히 15fps의 정수 프레임 수가 되지 않는다. 이때 `TimestampSegmentWriter.finish()`는 기존 1µs 허용 범위를 벗어난 끝을 거부한다. coordinator가 진행 중인 파일을 중단하므로, 이미 완료된 2초 파일 이후의 유효한 마지막 부분까지 전달되지 않는다.

첫 사건 `EVT_ffb4860f9f2b4262b12a08627640fa0e`의 원점은 `1794758451.614161`, 요청 끝은 `1794758463.144494`다. 실제 epoch-float 계산은 `(end-origin)*15 = 172.95499563217163`이며 가장 가까운 173프레임 끝보다 `0.0030002593994140625`초 이르다. 기존 1µs 허용 범위의 약 3,000배다. 다섯 개 2초 파일 이후 마지막 1.530333초가 불완전으로 보고됐다.

나머지 서로 다른 간격은 약 `225.12`, `277.015005`, `247.030005`프레임이다. 각각 PPE 두 종류의 사건에 나타났다. 정확한 일곱 사건, 요청 범위, 기존 완료 범위와 원래 SQLite transition의 숫자는 [실제 계산 JSON](../reports/capture-grid-actual-math-20261116.json)에 있다. 이는 프레임 경계 오류의 재현 근거이며 사람 수, 정탐 수 또는 연속 노출의 증명이 아니다. 별도의 두 `event_capture_incomplete`와 한 `missing_prebuffer_or_discovery_order`를 이 오류로 분류하지 않는다.

## 명시적인 새 계약

새 연속 실행은 Start에 정수 `capture_output_fps`를 보낸다. 지원 범위는 1..120이며 실제 writer 설정과 일치해야 한다. 이 필드는 기존 `processing_event_lifecycle.start_payload` JSON에 저장된다. 스키마 변경은 없다.

공유 순수 함수 `backend.capture_grid.required_capture_end()`는 위반 시작−2초를 원점으로 하고, 위반 종료+2초를 덮는 첫 출력 경계를 계산한다. 기존의 1µs 외곽 wire 허용 범위에 들어오는 경계는 같은 경계로 유지한다. 그 밖에는 최대 한 출력 프레임 미만의 실제 post-roll을 더 요구한다. 계산은 wire와 같은 정수 마이크로초를 사용한다.

관찰의 `timestamp_end`, 관찰 시간, 관찰 공백과 샘플 수는 바뀌지 않는다. backend의 요구 끝 조회, segment 범위 검증, complete close 검증, capture report 검증과 producer의 종료·전송·재시작 검증이 같은 계약을 사용한다. source timestamp를 연결하거나 덮어쓰지 않고, writer의 정수 프레임 경계 검사도 그대로 유지한다. coordinator는 실제 encoder 원점의 경계를 사용하고 전송 시에만 기존 마이크로초 wire로 직렬화한다.

실제 source frame이 새 경계에 도달하기 전에는 완료하지 않는다. 경계를 넘는 source gap도 기존 최대 input gap보다 크면 불완전이다. EOS나 post-roll 부족 역시 불완전이다. 기존 20ms 청크 연속성, clock 불확실성, 모델·PPE·자동 확정 정책은 변경하지 않는다.

필드가 없는 과거 Start는 기존의 정확한 종료+2초 계약을 유지한다. `None`인 새 필드는 canonical payload에서 제외하므로 이전 Start 바이트와 idempotent retry가 유지된다. 과거에 불완전으로 확정된 보고서를 새 계약으로 완료 처리하지 않는다. 새 producer는 새 backend와 함께 배포해야 한다. 기존 backend는 알 수 없는 Start 필드를 거부한다.

`deploy/api.Dockerfile`은 이미 backend 디렉터리 전체를 포함하므로 공유 함수도 API 이미지에 포함된다. 새 모델이나 외부 provider는 필요하지 않다.

## 검증 범위

검증은 임시 SQLite, 합성 프레임과 로컬 FFmpeg, HTTP mock을 사용했다. 진단을 위해 종료된 capture SQLite 저널을 읽기 전용으로 조회했다. 실제 카메라·GPU 추론·외부 provider를 실행하지 않았으며 운영 PostgreSQL DB와 서비스 설정은 변경하지 않았다. API TestClient는 Windows sandbox의 socketpair 대기 때문에 실행 권한을 확장해 검증했으며, API DB는 테스트별 임시 파일이었다.

- 실제 일곱 UTC 끝과 원래 journal의 네 가지 서로 다른 raw epoch 간격, 정확한 정수 프레임 끝과 기존 마이크로초 round trip.
- 위상이 다른 source frame의 실제 다음 관찰을 기다린 후에만 complete, 마지막 segment와 capture report의 범위 일치.
- 다음 관찰 없음, EOS, 확장 경계를 넘는 실제 input gap은 계속 incomplete.
- 새 Start의 요청 범위 조회·manifest·close·capture report 일치, 허용 범위 밖 segment 거부, 원래 위반 종료 시각 보존.
- 과거 Start canonical 바이트·재전송, 지원하지 않는 FPS 거부, 실제 비정수 종료를 가진 opt-in 세션의 재시작 후 settled no-op.

최종 집중 실행: producer·writer·재시작 64개 테스트 통과, API·operation receipt·관찰 정밀도 22개 테스트 통과. 그 전에 API·legacy·wire coverage 37개와 producer 관련 70개 회귀도 통과했다. 후속 수정과 겹치는 테스트 수를 합산하여 고유 테스트 수처럼 표기하지 않는다. `git diff --check`도 통과했다.

후속 정밀도 검토에서 지원 범위 안의 117fps 사례를 추가했다. 시작 `1794759195.0032425`, 관찰 종료 `start+3.49`에서 공유 함수의 wire 끝은 `16:13:20.498968Z`, 원래 encoder grid를 보존한 wire 끝은 `16:13:20.498969Z`다. 차이는 정확히 1µs지만 epoch float끼리 빼면 약 1.192µs가 되어 재시작 검증이 잘못 거부했다. 재시작의 외곽 범위·완료 범위·전송 계획 검증과 보존 파일 범위 조회를 canonical datetime/timedelta로 비교하도록 수정했다. 허용치는 기존 1µs 그대로이며 내부 capture gap과 manifest adjacency에는 허용치를 적용하지 않는다. 실제 수치의 settled no-op, 2µs 계획 차이 거부, 1µs 내부 공백 거부, 보존 파일 범위를 포함한 재시작 테스트 31개가 통과했다.

이 결과는 합성 회귀와 메타데이터 계약의 검증이다. 기존 실행의 사라진 영상 복구 또는 실제 카메라 재실측 성공을 뜻하지 않는다.
