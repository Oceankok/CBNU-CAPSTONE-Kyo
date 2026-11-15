# 완료된 과거 사건의 재시작 차단 수정 — 2026-11-15

## 문제와 변경 범위

독립 검증 후보 `be5ef7644dafb5ac4e029f35a4e8934d1530ce66`의 네이티브 재현에서 정상 완료된 사건 두 건, 요청 ACK 16개, 영상 조각 8개, 원본/정리/고아 의무 0개인데 관리자 검토 후 다음 child가 `protected_historical_event`로 종료했다. 일반 완료 경로는 `recovery_states=reconciled`를 기록하지 않는데, 기존 단축 경로가 이 표시를 요구했기 때문이다.

기존 `7229a73`은 수정하지 않고 그 뒤의 별도 로컬 커밋으로 보완한다. 제품 변경은 `inference_worker/session_recovery.py`의 과거 이력 단축 판정뿐이다. 백엔드·모델·판정 임계값·설정 키·wire protocol·자동 확정은 변경하지 않는다. 자동 확정은 계속 OFF다.

## 안전한 무작업 판정

원래 endpoint에 대한 모든 과거 세션의 검증을 먼저 유지한다. 이후 `SessionRecovery._settled_history`가 다음 근거를 모두 확인하면 보호된 과거 사건 조회/전송을 건너뛴다. 이 판정 자체는 SELECT와 파일 존재/경로 검사만 수행하고 HTTP 호출·저장 이력 수정·파일 삭제를 하지 않는다.

- 사건이 terminal `closed` 또는 명시적 `incomplete`이고, 원래 세션의 close intent가 finalized다.
- 요청 연속성·원문 SHA256·event/session/sequence/camera가 맞는 ACK가 모든 명령에 있다. 단순히 pending queue가 비었다는 이유로 완료로 보지 않는다.
- 사건당 정상 complete close 또는 명시적 incomplete close가 정확히 하나 있고, ACK lifecycle phase가 각각 closed/interrupted와 일치한다.
- immutable snapshot의 모든 필수 카메라에 terminal capture report ACK가 있다. captured/missing 구간이 최종 필수 구간 전체를 설명한다.
- manifest prefix와 camera/role/request/시각 연결이 일치한다. complete이면 전체 구간을 덮고, incomplete이면 미전달 구간이 ACKed incomplete report의 missing 범위에 포함된다.
- uploaded_private 원본마다 원래 세션·manifest·파일 경로·hash·bytes·촬영/필수 범위와 연결된 accepted 영수증이 있다. 기존 cleanup_receipt_verified 검증도 그대로 사용한다.
- unavailable 원본은 명시적 durable 실패 사유와 해당 카메라의 ACKed incomplete/missing 처분이 있으며 cleanup=0, 파일 부재다. 업로드 행의 모호한 상태와 함께 완료로 취급하지 않는다.
- pending metadata/upload/cleanup/close, 고아·불명확한 소유권·미정리 source가 없다. 모든 소유 spool 디렉터리가 release되어 없다. 알 수 없는 파일을 제거하거나 TTL을 연장하지 않는다.

일반 정상 완료는 복구 표시 없이도 위 근거로 건너뛴다. 이전 reconciled 표시가 있더라도 근거가 부족하면 기존 보호 경로를 적용한다. 참고 카메라가 처음부터 실패하여 원본/스풀이 없는 일반 incomplete 사건도 모든 카메라의 명시적 보고가 ACK되고 남은 작업이 없을 때만 건너뛴다.

일반 ACK의 최상위 lifecycle뿐 아니라, COMPLETE 응답 유실 복구가 저장한 operation_receipt도 검증한다. 저장된 증명이 committed_exact이고 event/session/sequence, submitted/accepted SHA, 정규화된 원래 body 내용, lifecycle revision/phase가 모두 일치할 때만 terminal ACK로 인정한다. recovery marker만으로 이 검사를 생략하지 않는다.

시간 비교는 기존 wire의 마이크로초 표현에 맞춘다. 원본 segment/plan의 시각을 임의로 수정하지 않는다.

## 검증

| 실행 | 결과 | 범위 |
|---|---|---|
| settled-history + 기존 로컬 recovery | 26 tests / 8.009s / 실패·제외 0 | marker 없는 complete, missing reference/discarded/zero-source incomplete; 누락 보고·미ACK·raw/unknown 파일 보존·cleanup·영수증/plan/session 불일치·orphan/소유권·미종료 close·stale marker·conflict·endpoint 차단; historical operation proof의 SHA/body/event/revision/phase 오류 |
| 신규 producer ASGI/PostgreSQL | 3 tests / 41.504s / 실패·제외 0 | 실제 ContinuousFrameRuntime/FFmpeg/업로드/원본 정리 후 검토·무작업 재시작; COMPLETE 응답 유실→첫 복구→검토→두 번째 재시작; incomplete 처분과 기존 검토 제한 |
| 기존 API recovery/transport/EOS | 27 tests / 21.424s / 실패 0 / Windows capability skip 1 | 기존 원래 세션 복구 6개, 전송·종료 회귀; skip은 실제 WinError1314 symlink 권한 부재 |

신규 complete producer 테스트는 실제 인증된 API로 synthetic result provider를 통해 처리 결과를 게시하고 confirmed 관리자 검토를 저장한다. 원래 close의 read-only receipt가 already_reviewed를 반환하는 것을 먼저 확인한 뒤, 새 outbox 세션의 복구가 HTTP 호출 0회 및 로컬 journal 변경 0회로 끝나는 것을 확인했다. 실제 탐지/얼굴 모델의 성능을 검증한 것은 아니다.

추가 시나리오는 실제 producer의 COMPLETE가 서버에 저장된 직후 응답을 유실시키고 프로세스를 종료한다. 첫 재시작은 인증된 read-only receipt로 과거 close를 ACK한 뒤 남은 원본을 업로드하고 기존 소유 spool 정리로 raw를 제거한다. 이때 close ACK에는 최상위 lifecycle 없이 operation_receipt가 저장되는 것을 확인한다. 이후 실제 confirmed 검토를 저장하고 두 번째 재시작이 HTTP 호출 0회/로컬 이력 변경 0회인 것을 확인했다.

일반 incomplete producer는 참고 카메라를 source_failed로 끝낸 후 필수 snapshot/보고를 유지하면서 원본 정리까지 배출한다. 복구는 HTTP 호출 0회로 끝난다. 현재 백엔드는 require_finalizable에서 interrupted 사건의 모든 관리자 검토를 `409 continuous_event_not_closed`로 거절한다. 따라서 현재 API에서 '일반 incomplete 사건을 사람이 검토한 뒤 재시작'은 그보다 앞선 검토 단계에서 막힌다. 이 정책은 변경하지 않았다. 이미 보호된 상태를 가정한 incomplete 로컬 회귀에서도 미완료 의무가 있으면 차단되고, 완전히 처분된 이력만 무작업 처리된다.

독립 검증자의 원본 `single2-state` DB 두 개는 SQLite mode=ro로만 열었다. 두 사건 모두 새 판정을 통과했고 raw 폴더는 비어 있었으며 두 DB total_changes=0이다. 원래 before/after/runtime/stdout/원본 재현 자료는 수정하지 않았다.

원본 로그는 작업 폴더 `.private_media/completion-evidence/settled-history-local.log`, `settled-history-producer-api.log`, `settled-history-existing-regressions.log`, `settled-history-native-readonly.json`에 보존했다. 이전 451-test full suite는 7229a73 단계의 근거이며 이 후속 변경의 전체 suite 재실행으로 주장하지 않는다. root가 통합 후보의 Linux CI와 네이티브 reviewed-restart를 최종 재검증한다.

## 의도적으로 남긴 보수적 제한

이 변경은 모든 과거 이력을 무조건 완료로 판정하는 기능이 아니다.

- retired_requests가 있는 supersession/late-original 복구 이력과 orphan audit가 남은 이력은 일반 producer의 무작업 근거와 구분하여 기존 인증 복구 경로에 둔다. 검증된 committed_exact historical ACK 자체는 제외 사유가 아니다. 예를 들어 source-loss complete retirement 뒤 incomplete 대체가 ACK된 사건은 source가 없어도 이 단축 경로를 통과하지 않는다. 이후 원격 보호 상태가 되면 별도 운영 복구가 필요할 수 있다.
- raw는 없더라도 spool 메타데이터 디렉터리가 남은 crash-during-release 상태는 완전 release로 추측하지 않는다. 이 경우도 원격 보호와 겹치면 별도 복구가 필요할 수 있다. 원본 native reviewed fixture는 해당 디렉터리까지 모두 제거된 상태였다.
- 일반 interrupted/incomplete 사건을 관리자가 검토할 수 없는 현재 백엔드 제한은 별도 과제다. 새 판정이 검토 가능성이나 자동 확정 자격을 부여하지 않는다.

따라서 검증된 해결 범위는 원래 세션의 authoritative ACK/처분/영수증과 실제 release가 모두 확인된 일반 complete/incomplete producer 이력이다. 모호한 복구 이력까지 모두 해결했다고 주장하지 않는다.


## 자원 반환

집중 회귀 종료 후 전용 `kyo_test_inference_completion_20261115`의 테스트 schema 0, public table 0, 다른 연결 0을 확인했다. 이번 worktree/회귀 명령행으로 식별되는 Python/FFmpeg 잔여 프로세스는 0이다. cluster는 중지하지 않았고 원래 독립 검증 자료/스키마/서비스 설정은 변경하지 않았다. 사용 종료를 root와 cluster owner에게 전달한다.
