# 추적·PPE 연결·사건 저장 안정화 인계 (2026-11-15)

## 위치와 통합 순서

- 작업 폴더: `C:/Users/robin/Projects/CBNU-CAPSTONE-Kyo/.private_media/worktrees/inference-completion-20261115`
- 브랜치: `feat/inference-completion-20261115`
- 지정 기준: `33c39ae6bd22c3d6669dcf4cb4a5b7fa9fdc968a` (이전 PR257 등의 검토 후보 포함)
- 1차 구현: `f02dfa5`.
- 백엔드 operation-receipt 의존 커밋: `92b079b89fd5cc3140f0b96f3f06beb425818360`. 로컬 merge `c32cef5`에 포함.
- 이 문서와 최종 회귀 수정이 담긴 후속 커밋까지 함께 통합해야 한다. 기준에 없는 브랜치에 후속 커밋만 적용하면 안 된다.
- 원격 push/새 PR/main 병합/배포는 수행하지 않았다. root 통합 담당이 최종 의존 관계와 검토 절차를 결정한다.
- 이전 PR257 작업 폴더·원본 영상·가중치·탐지 캐시는 그대로 보존했다. 기존 미커밋 실험을 덮어쓰지 않았다.

## 담당 범위와 완료한 동작

자동 확정은 OFF다. 사람 탐지 모델 재학습, 가시성/정확도 기준 완화, 카메라 간 사건 자동 병합은 포함하지 않는다. 사용자가 요청한 기준은 같은 카메라에서 같은 사람의 같은 미착용이 계속되면 한 사건, 안정적으로 재착용했다가 벗으면 새 사건이다.

이전 PR257의 ByteTrack·시간 기반 유지·일대일 PPE 연결·unknown 처리·재착용 확인·SQLite exact-body 재시도를 이번 지정 기준에서 사용한다. 약한 사람 탐지는 기존 ID 유지에만 사용하고 새 사람/미착용 근거로 쓰지 않는다. 경합 또는 잘린 영역은 unknown이다. 서로 다른 ID를 추측해 합치지 않으므로 완전 가림/장기 이탈 이후 동일인 보장은 없다.

이번 후속 구현은 실제 runner와 복구 경계의 남은 결함을 보완한다.

1. **모델 출력과 판정 경로**: class_id/class_name/confidence/bbox의 실제 점수·좌표·클래스 연결을 검증한다. 잘못된 PPE 출력이 사라져 미착용 근거가 되지 않도록 프레임을 unknown으로 보존한다. 점수가 없는 사람은 추적 입력으로 꾸며 넣지 않는다. review 모드는 no_helmet 클래스 존재와 무관하게 안전모·조끼를 판정하고, verified_helmet은 실제 no_helmet 지원이 필요하다. 모델 confidence를 미착용 확률로 바꾸지 않는다.
2. **legacy 저장/영상 상태**: 같은 event_id와 원본 body를 재시도한다. clip 준비·종료 실패 의도를 메타데이터로 먼저 기록하여 프로세스 재시작, 소스/정책 변경, post-roll 부족 뒤 unavailable 상태를 제한된 재시도로 보고한다. received/failed/unavailable 상태를 덮어쓰지 않는다. 미디어 원본을 새로 영속화하지 않는다.
3. **continuous 종료**: pre-roll 부족이 사건 생성 자체를 막지 않는다. 실제 관측으로 사건을 만들고 영상은 incomplete로 기록한다. stop/EOS/max-chunks/예외 종료는 실제 사유로 lifecycle/capture를 정리하고 제한된 delivery tail을 수행한다. 미해결 의무는 성공 종료로 숨기지 않는다.
4. **참고 카메라 장애**: 참고 소스 미등록·첫 프레임 시간초과·중간 중단·카메라별 timeout/5xx·decode 실패가 주 카메라 추론을 막지 않는다. 설정된 필수 카메라는 snapshot에 그대로 남고 해당 증거는 incomplete다. 최초 프레임 유예 종료를 runner stall 검사 전에 적용한다. 401/403, 주 카메라 실패, 전역 stream 목록 실패는 계속 전체 실패다. 실패 참고 소스의 자동 재접속은 현재 세션에서 하지 않으며 새 세션이 필요하다.
5. **원래 세션 복구**: 새 프로세스는 새 추적/세션으로 시작한다. 먼저 모든 이전 세션의 저장된 endpoint를 검증하며 하나라도 누락/불일치하면 외부 호출은 0회다. 이후 해당 사건만 dispatch하여 다른 보호된 사건의 대기 요청을 함께 보내지 않는다. API operation-receipt로 원래 close의 실제 접수를 확인하고, 정확한 과거 요청만 ACK한다. source가 없고 원래 complete가 미접수이면 원문을 변경하지 않고 감사 가능한 retirement와 incomplete 대체 요청을 원자적으로 추가한다. 늦은 원래 complete 접수가 이기면 덮어쓰지 않는다. 열린 사건은 마지막 영속 관측으로 incomplete 종료한다.
6. **남아 있는 원본**: 원래 세션의 등록된 파일·hash·범위·서버 manifest가 맞으면 새 사건을 만들지 않고 원래 close/업로드를 완료할 수 있다. 보호/충돌/출처 불명/매핑되지 않은 raw는 recovery-required로 중지한다. TTL을 늘리지 않는다. 소유권이 확인된 과거 spool만 기존 영수증/불완전 증거 정책에 따라 정리한다.
7. **Windows decode**: raw 파일 os.write에 O_BINARY를 적용해 CR/LF 변환으로 MP4 bytes/hash가 달라지는 문제를 수정했다. 다른 파일 경로의 이진 쓰기도 확인했다.
8. **교체 모델 검증 준비**: 엄격한 scored_xyxy_v1 manifest·전체 artifact SHA·class map·탐지 캐시 검증 및 주입 가능한 replay harness를 추가했다. 새 가중치/새 클래스/두 모델 결합/점수 없는 출력은 확인 전 자동 지원하지 않는다.

## 파일과 소유권

- 추적/연결/사건: `person_tracker.py`, `ppe_rules.py`, `event_lifecycle.py`, `violation_tracker.py` (이전 PR257 포함).
- 입력 계약: `detection_contract.py`, `assessment_pipeline.py`, `ppe_detector.py`, `validation_harness.py`, `replay_episode_comparison.py`.
- 실행/저장: `run_server.py`, `run_continuous.py`, `pending_events.py`, `continuous_runtime.py`, `capture_outbox.py`, `session_recovery.py`, `continuous_dispatcher.py`, `continuous_transport.py`.
- source/증거: `continuous_source_reader.py`, `continuous_source_mux.py`, `multicamera_capture.py`, `private_chunk_decoder.py`, `eos_delivery.py`.
- G3 소유 `run_multi.py`, `reference_collector.py`, host launcher/settings/preflight는 수정하지 않았다. G3의 프로필 실행 변경과 root에서 함께 통합한다.
- 백엔드 operation-receipt API는 별도 담당 구현을 의존 커밋으로 로컬 병합했다. API 설명은 `docs/20261115_Continuous_Operation_Receipt.md`.

## 실행·재검증 계약

기존 child CLI와 continuous 설정 최상위 키는 유지한다. 상세는 같은 폴더 `20261115_inference-runtime-contract.md`.

한 continuous 프로세스는 주 카메라 하나를 추론하고 설정된 참고 카메라는 증거만 수집한다. 모든 카메라에서 탐지하려면 G3의 서로 다른 primary/state 디렉터리 프로필을 각각 실행한다. 같은 프로필에 reference_collector를 중복 실행하지 않는다. 주 카메라 하나와 참고 0개도 지원하되 서버가 돌려주는 필수 snapshot은 정확히 일치해야 한다.

재시작에는 같은 상태 디렉터리와 원래 endpoint가 필요하다. durable metadata는 사람 신원·진행 사건·클립 프레임 버퍼를 복원하지 않는다. 원래 요청의 역사적 접수와 현재 영상 준비/검토 가능 상태는 별개다.

## 검증 근거

- **전체 추론 suite: 451개, 67.650초, 실패/오류 0, skip 3.** 실제 전용 PostgreSQL과 FFmpeg를 사용했다. 해당 실행 이후 제품 코드는 바뀌지 않았으며 열린 사건의 추가 API 회귀 테스트만 추가했다.
- **API 복구 모듈: 6개, 20.222초, 모두 통과.** TestClient/ASGI bridge + 실제 PostgreSQL, 인증/원래 세션/원문 유지/늦은 commit/응답 유실/원본 소실/incomplete/남은 양쪽 카메라 원본 업로드/열린 사건의 마지막 관측 종료를 확인했다. 실제 TCP 서버·운영 카메라는 사용하지 않았다.
- skip 3개는 WinError1314로 symlink 생성 권한이 없는 경우에만 건너뛴다: `test_private_path_validation`, `test_symlink_rejected`, `test_unknown_raw_file_and_symlink_not_deleted`. Linux 또는 권한 있는 Windows에서 보안 경계를 추가 검증해야 한다. 다른 예외는 계속 실패한다.
- 전체 suite에는 유지 중 1건, 재착용 후 새 사건, 긴 visible unknown, 약한 탐지, 겹침/일대일 연결, 저장 재시도, source/종료 회귀가 포함된다. 이를 실영상 정확도나 모든 신원 교차 상황의 성공으로 확대하지 않는다.
- 증거 로그: 작업 폴더 `.private_media/completion-evidence/final-full-pg-inference.log`, `final-recovery-api-pg.log`, `final-recovery-boundaries.log`. 로그는 git에 올리지 않았다.
- 전용 DB: `kyo_test_inference_completion_20261115`, owner가 유지하는 로컬 cluster를 사용했다. 테스트는 UUID별 schema에 격리했다. 운영 DB/영상/카메라 설정은 변경하지 않았다. 최종 확인에서 남은 테스트 schema 0, public table 0, 다른 DB 연결 0이었다. 이번 작업 폴더/합성 테스트를 명령행으로 식별한 Python/FFmpeg 잔여 프로세스도 없었다. cluster는 중지하지 않고 owner에게 사용 종료를 알린다.

## 남은 통합·측정

1. root가 최종 SHA를 G3/백엔드 후보와 로컬 통합하고 검증한다. 자동 확정은 계속 OFF다.
2. 교체 가중치가 제공되면 실제 manifest·점수·클래스·좌표를 먼저 확인한 뒤 동일 영상/정답으로 replay한다. 이번 작업에서 새 가중치나 GPU 재생은 수행하지 않았다.
3. 기존 모델 실영상 비교는 이전 PR257 문서의 역사적 근거다. Camera02 분할 개선 외 사람 오탐·Camera01 지연·조끼 누락은 남아 있었으며 이번 입력/복구 보완으로 정확도 개선을 주장하지 않는다.
4. 운영 환경에서 끊김/복구/착용-탈착/여러 사람 교차, 이벤트 생성부터 영상 연결·UI 재생까지 확인한다. 합성 API 업로드와 UI 재생은 동일한 증거가 아니다.
5. source failure/미해결 복구는 담당자에게 diagnostic으로 남기고 별도 처분한다. 보호된 사건·출처 불명 원본을 자동 수정하거나 사건을 합치지 않는다.
