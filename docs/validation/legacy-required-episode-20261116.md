# Legacy 검토 사건의 ROI 이동 중 중복 방지

기준은 main `b0c0f1aec1caa0c24bbad6aea0c51c74a6169d23`이다. 이번 변경은 `--submit-review-candidates` legacy 경로에만 적용한다. 모델·추적 ID 할당·자동 확정·정책·사용자가 삭제한 ROI 설정·운영 DB는 수정하지 않는다.

## 재현과 변경

기준 코드에서 동일 camera/track의 안전모·조끼 사건을 먼저 생성하고, 실제 `resolve_person_policy`에 같은 PPE 의무가 유지되는 안/밖 위치를 4초마다 전달했다. 최초 2건 이후 추가 start 6건(각 PPE 3건)이 발생해 검사가 실패했다. 프레임마다 경계를 흔드는 예비 입력에서는 qualification이 계속 취소되는 것도 확인했다. 이는 합성 코드 재현이며 실제 영상 전체의 정답 평가는 아니다.

기존 lifecycle은 area_ids까지 포함한 scope 키가 바뀌면 사건을 종료했다. 이제 legacy 검토 adapter는 **이미 후보가 생성된 동일 camera/track/PPE**에서 다음 조건이 모두 유지되면 최초 사건의 scope와 event_id를 유지한다: 명시적으로 해석된 정책의 version·scope 기준·증거 모드가 같고, 그 PPE가 이전/현재 모두 필수다. 다른 PPE의 의무 변화는 해당 PPE만 종료한다. 원본 scope는 사건의 고정된 기준이며 사람의 현재 ROI 소속이라고 주장하지 않는다.

후보 생성 전에는 기존 full-scope 경계와 3초 관측 조건을 유지한다. 실제 정책 버전 변경, PPE 의무 해제, 관측된 재착용, 사람 이탈/새 사람은 기존 사건 경계를 유지한다. PPE 의무가 해제된 뒤 다시 필수가 되면 새로 3초 관측 후 새 사건을 만든다. 위치/정책 해석 불가인 None은 unknown으로 유지하며 명시적 의무 해제와 구분한다. runner는 알려진 비필수 정책도 scope로 전달해 모두 비필수인 상태가 unknown처럼 사건을 무한 유지하지 않게 한다.

PPE별 사건 기준이 다를 수 있으므로 adapter는 각 PPE를 별도 observation row로 shared lifecycle에 전달한다. shared `EventLifecycle`와 continuous runner/payload/정책 hash/자동 확정 규칙은 변경하지 않았다. 기본 ViolationTracker도 기존 strict 동작이며 legacy 검토 runner만 명시적으로 opt in한다. 저장 요청 body와 event_id를 갱신·병합하지 않으며 최초 qualification 시간 구간, capture_context 및 서버의 immutable zone 귀속을 재배치하지 않는다.

## 검증

신규 10개 검사와 기존 lifecycle/episode stability/unknown scope/diagnostics/server mock/episode runner/ROI runner를 합쳐 **93개 통과**, `git diff --check` 통과했다. 모델·실제 서버·새 촬영 없이 합성 관측과 기존 fake API를 사용했다. 실제 ByteTrack을 포함한 runner 검사에서 점진적으로 경계를 왕복하는 같은 ID는 안전모·조끼 2건을 유지했고, equipment-only 의무의 안→밖→안은 같은 ID에서 총 4건을 만들었다.

검사 항목은 같은 의무 중 소속 변경, 초기 qualification 리셋, PPE별 의무 해제/복원 독립성, 전체 의무 해제와 unknown 구분, 정책 버전 변경, 재착용·사람 이탈·새 사람, 증거 모드 변경, 기본 adapter/continuous의 strict 유지, 실제 runner의 검토 전용 payload와 알려진 비필수 구역 처리다.

검사 작성 중 전이 목록의 순서만을 잘못 기대한 assertion을 순서 독립 비교로 수정했다. 초기 runner fixture의 순간적인 bbox 이동은 실제 tracking ID를 바꾸어 정상적으로 새 사건이 생겼으므로 점진적 이동으로 바꿨다. 비필수→필수 fixture도 복귀 후 3초가 되기 전에 끝나던 길이를 늘렸다. 이 과정에서 운영 임계값이나 추적 기준을 완화하지 않았다. 최종 93개 결과는 수정된 입력의 결과다.

```powershell
python -B -m unittest inference_worker.test_legacy_required_episode inference_worker.test_episode_close_diagnostics inference_worker.test_event_lifecycle inference_worker.test_episode_stability inference_worker.test_unknown_policy_scope inference_worker.test_server_mock inference_worker.test_episode_runner inference_worker.test_roi_runner_end_to_end -q
```

이번 수정은 확인된 동일 의무/구역 범위 변경 경로를 다룬다. 장시간 사람 미탐, 완전 가림 후 ID 교체, 실제 다른 사람 구분, 프로세스 재시작 이후 추적 연속성, 앉은 자세의 관측 불가까지 해결했다고 주장하지 않는다. 과거 30건 전체를 중복으로 재분류하지 않는다. 실제 입장/정지 위치와 박스 흔들림은 별도 관측 문제다. 운영 적용과 실영상 비교는 담당 세션의 검토 이후에 진행한다.
