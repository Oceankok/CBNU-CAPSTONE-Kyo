# PPE 추적·사건 작업 인계 — 2026-11-16 KST

작업 범위는 사람 추적, 사람과 보호구의 연결, 같은 카메라의 같은 미착용 사건 유지, 이벤트 메타데이터 저장 재시도다. 자동 확정, 모델 재학습, 운영 설정·DB·런처 변경은 포함하지 않는다.

기존 구현은 `dbe64ee7f0c641df135ef1ce23bedb110bd84471`이며 현재 확인한 배포 소스 `bd29d8f70dd0a4b49fc4703cc9da2586b96b4f08`에 포함돼 있다. 원래 `codex/event-episode-lifecycle-20261113` 실험과 이번 코드는 별개이며 실험 폴더를 수정하지 않았다.

| 항목 | 현재 코드의 동작 | 한계 |
|---|---|---|
| 사람 추적 | 카메라별 ByteTrack, 약한 탐지는 기존 ID 유지에만 사용, 겹침은 PPE 판정 보류 | 외형 ReID 없음, 새 ID를 무조건 병합하지 않음 |
| 보호구 연결 | 머리·몸통 위치를 이용한 일대일 할당, 애매한 연결·관측 불가를 unknown 처리 | 사람 모델 오탐과 가장자리 잘림은 잔존 가능 |
| 사건 유지 | 실제 관측량 3초로 후보 생성, 같은 ID의 visible unknown은 진행 사건 유지, 0.5초 재착용 확인 후 새 탈착은 새 사건 | 사람 누락 1초 초과·정책/소스/프로세스 변경은 별도 경계 |
| 저장 재시도 | SQLite outbox에 event_id와 body를 먼저 저장, 같은 요청 재시도, 영구 거절 보존 | 메타데이터 복구이며 추적·진행 사건·영상 버퍼 복구는 아님 |

승인 기준은 **같은 카메라·같은 사람·같은 미착용 지속은 1건, 재착용 확인 후 다시 벗으면 새 1건**이다. 카메라 간 중복 의심 태그는 이 기준의 대체 기능이 아니다. 현재 review-only 판정은 유지한다.

## 실제 시험에서 확인된 미해결 사항

기존 `reports/server-deploy-20261115/live-basic-current-check-summary.json`의 UTC `2026-11-15T19:03:15Z` 스냅샷에는 약 3분 동안 후보 30건이 있다. CAM02는 동일 track-1에서 안전모 8건·조끼 8건이다. CAM01은 안전모 9건·조끼 5건이며 track-1/2가 있다. 수신 sequence gap은 두 카메라 모두 0이다. 이후 사용자는 노트북 내장 웹캠에는 본인과 룸메이트 **두 사람**, 뒤에 설치한 외부 카메라에는 **본인 한 사람**이 보였다고 정정했다. 내장/외부 장치와 CAM01/02의 대응은 검증하지 않았다. 기존 요약의 한 사람 가정은 폐기하며, CAM01의 두 ID는 서로 다른 실제 사람일 수 있다. 개인별 출입·재착용·가시성과 실제 PPE 착용 상태는 이 기록만으로 확정하지 못했다.

따라서 30건 전부를 중복으로 분류하거나 실제 중복 방지 실패를 확정하지 않는다. 동일 ID/PPE 반복에는 시간·재착용·가시성 분석이 필요하며 실환경 중복 방지 통과도 입증되지 않았다. 기존 stdout은 초기 정책 reset과 stream 경계 뒤 같은 ID 후보가 다시 생긴 사실을 보여 주지만, 각 사건의 종료 전이를 버리므로 정확한 종료 이유는 남아 있지 않다. 수신 gap 0만으로 탐지·추적 관측이 연속적이었다고 판단할 수도 없다.

코드상 후보는 주기적 3초 타이머가 아니라 lifecycle의 start에서만 나온다. 같은 ID의 PPE unknown만으로는 진행 사건을 종료하지 않는다. 새 후보 전에는 관측 누락, 재착용 확인, 정책 범위 변경, 증거 모드 변경 등 종료 조건이 필요하다. 어느 조건이 실제 발생했는지는 현재 기록만으로 확정하지 못했다.

부분/빈 PPE map이면 보이는 사람도 해당 PPE 관측 누락으로 처리하는 counterexample은 있다. 그러나 현재 review 생산 경로의 정상 평가와 `unknown_people` 모두 helmet/vest를 함께 생성한다. 실제 도달 경로를 확인하지 못했으므로 이 약점을 이번 원인으로 주장하거나 상태 기계를 수정하지 않는다. 영상의 착용 모습만으로도 내부 종료 전이의 이유까지 복원할 수는 없다.

## 이번 추가 수정안

별도 브랜치 `fix/episode-close-diagnostics-20261116`, 기준 bd29에서 legacy adapter가 버리던 **확정된 후보의 close 전이만** 선택적 callback으로 전달한다. 실제 runner는 `[EPISODE CLOSE]` JSON 한 줄을 남긴다. 필드는 camera_id, tracking_id, ppe_type, event_id, reason, start_time, end_time, observed_at이다. 시간은 source UTC epoch seconds이며 observed_at은 종료 조건을 평가한 시각, end_time은 마지막 유효 미착용 시각이다.

프레임별 progress/unknown 로그는 만들지 않고 원본 프레임·박스·정책 범위·서버 요청 body도 기록하지 않는다. 종료 한 번당 한 줄이며 프로세스 전체 로그 용량 상한/회전 기능은 이 변경에 추가하지 않았다. 명시적인 runner reset과 프로세스 종료는 이 callback 범위 밖이며 기존 reset/stream 기록과 함께 해석해야 한다.

사건 생성·유예·재착용·정책·저장 조건은 변경하지 않았다. **이는 원인 확인을 위한 관측성 보완이며 실제 중복 원인 수정 완료가 아니다.** 최초 인계 시점에는 운영 폴더에 적용하거나 병합·배포하지 않았다. 이후 사용자는 CI 통과 후 정상 배포와 재시험을 승인했다. 적용 과정도 기존 자동 확정 정책을 수정하지 않으며, manual review 경로의 자동 확정 비대상 조건을 전역 정책 OFF로 해석하지 않는다. 최초 진단 작성 중 새 촬영, 서버 재생, 모델 추론, DB/API 조회를 추가 실행하지 않았다.

## 검증과 다음 담당 작업

신규 진단 검사 최초 실행은 callback 미구현으로 2 error / 1 pass였다. 이는 기존 중복 버그의 재현 결과가 아니라 새 진단 기능의 구현 전 결과다. 구현 후 신규 4개와 기존 lifecycle/episode stability/unknown policy/서버 mock을 합쳐 **72개 통과**했다. 실제 카메라, 모델, 서버 없이 합성 관측을 사용했다. 중간 실행에서 존재하지 않는 `test_violation_tracker` 모듈을 지정해 import error가 있었고 실제 모듈 목록으로 수정했다. 해당 실패를 통과로 집계하지 않는다.

추가로 API/poll 실패와 저장 거절을 다루는 기존 `test_episode_runner` 5개도 통과해 총 **77개 통과**다. `git diff --check`도 통과했다.

재현 명령(전용 dependency 디렉터리를 PYTHONPATH에 사용):

```powershell
python -B -m unittest inference_worker.test_episode_close_diagnostics inference_worker.test_event_lifecycle inference_worker.test_episode_stability inference_worker.test_unknown_policy_scope inference_worker.test_server_mock -q
python -B -m unittest inference_worker.test_episode_runner -q
```

통합 담당은 이 진단 변경을 검토하고 별도로 적용 여부를 결정한다. 다음에 확보되는 정상 운영 기록에서 동일 camera/track/PPE의 이전 event_id close와 새 start를 연결해 이유를 구분한다. observation_gap이면 실제 사람 측정 누락인지, ppe_present이면 실제 재착용 또는 보호구 잘못 연결인지, policy_scope_changed이면 실제 구역 이동 또는 박스 흔들림인지 확인한 다음 해당 경로만 수정한다. 기존 기록의 원인을 소급 확정하거나 이벤트 수를 줄이기 위한 cooldown·유예 연장·무조건 병합은 하지 않는다.

별도 sender cadence PR #268은 이 변경과 독립이다. 이 문서와 진단 검사의 통과를 실제 중복 방지, 영상 전체 준비 완료, 자동 확정 또는 배포 승인 증거로 사용하지 않는다.
