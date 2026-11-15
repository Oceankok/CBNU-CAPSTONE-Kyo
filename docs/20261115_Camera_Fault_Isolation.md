# G3 카메라 장애 격리 및 증거 누락 표시

## 변경 범위와 운영 적용

기준은 `ac9d254774ae5c1d0ebdc9019f249b7ddceb68e8`입니다. 해당 버전은 CLI 재시도 기본값이
0이며 한 슬롯 종료 또는 예산 소진 시 전체 종료합니다. 기존 단위 검사 16개로 이 동작을 확인했습니다.

새 CLI 기본값은 `isolate`, camera/reference 각각 실행 전체 3회·5초입니다. 재시도 소진 후 건강한
카메라는 유지하고, 정상 종료한 슬롯은 재시작하지 않으며 모든 카메라가 비활성화되면 전체 종료합니다.
영구 인증/요청 오류(exit 78), argparse 설정 오류(exit 2), 최초 생성 실패는 전체 종료합니다.
공유 모델 로딩·클래스/모드·tracker 초기화 및 카메라 정책 인증은 추론 자식 시작 전 별도 프로세스에서
검사합니다. 이 프로세스는 종료 후 GPU/모델 메모리를 놓으므로 supervisor가 세 번째 모델을 계속
보유하지 않습니다. 시작할 때 모델을 한 번 더 읽는 비용은 추가됩니다. 실제 GPU 재할당 실증은 별도입니다.
이벤트 생성 PR #257과 통합할 때는 그 브랜치의 갱신된 requirements에 맞춰 추론 가상환경을 먼저
준비해야 합니다. 특히 Ultralytics/lap/SciPy 버전 요구를 충족해야 하며, tracker 초기화 및 빈 입력의 PPE 판정
(`assess_ppe([], (64, 64, 3))`)으로 지연 import 의존성도 preflight에서 확인합니다.
설치는 향후 사용자 승인 배포 단계에서 해당 Python 환경의
`python -m pip install -r requirements.txt -r inference_worker/requirements-tracking.txt`로 수행합니다.
추적 의존성 파일은 PR #257이 제공하며 현재 호스트 의존성은 변경하지 않았습니다.

검사한 호스트 `C:\Users\robin\KyoServer\Launcher\inference_entry.py`는 `arguments(settings)`에서
모델·버퍼·카메라·mode만 만든 뒤 저장소 `run_multi.main`을 호출합니다. 따라서 해당 소스가 통합·배포되면
런처 파일 수정 없이 새 CLI 기본 정책을 사용합니다. 라이브 런처나 공유 main을 이 PR에서 바꾸지 않았습니다.
운영에 적용됐다는 의미가 아니며, 현재 작업은 검토용 브랜치에만 있습니다.
실제 호스트 파일을 읽기 전용 import하여 `arguments`가 생성한 인자를 수정된 `main`에 전달했을 때
`isolate`, camera/reference 각각 3회·5초가 선택됨을 확인했습니다. 이 검사는 preflight와
supervisor를 대역으로 바꿔 실행했으며 실제 호스트 추론을 시작하지 않았습니다.

```powershell
python -m inference_worker.run_multi --camera-id CAM_A --camera-id CAM_B --model-path C:\Models\best.pt --submit-review-candidates
```

이전 즉시 전체 종료를 수동으로 선택할 때는 같은 명령에 다음을 추가합니다.

```text
--failure-policy fail-fast --camera-restart-limit 0 --reference-restart-limit 0
```

CLI에 양수 예산과 fail-fast를 함께 지정하면 예전 opt-in 계약대로 재시도 후 전체 종료합니다.
개인 런처 JSON에 새 키를 넣어도 현재 `arguments`가 전달하지 않으므로 설정으로 주장하지 않습니다.
정책 변경은 저장소 CLI 또는 추후 추적 가능한 런처 인자 전달 변경으로 적용해야 합니다.

## 증거 누락 경로

카메라/collector 프로세스가 사라져도 이미 접수한 이벤트·clip request는 DB에 남습니다.
미디어 없는 pending 요청은 생성 및 최근 활동으로부터 각각 300초가 지나고, segment deadline도
남아 있지 않을 때 maintenance가 `unavailable / clip_delivery_timeout`으로 바꿉니다.
maintenance가 실행되어야 전환됩니다. received 요청·접수한 미디어는 보존됩니다.
이는 즉시 `incomplete` 보고를 쓰는 기능이 아닙니다. 아직 생성되지 않은 사건의 누락은 기록할 수 없습니다.

`GET /api/events/{event_id}/media`의 `clip_requests`가 상태와 오류를 반환합니다. 기존 UI는 이 필드를
사용하지 않았고, primary 자료가 하나라도 있으면 `review_availability=available`이라 참고 영상 누락을
보여 주지 않았습니다. 새 UI는 카메라별 대기·처리 중·수집/처리 실패·확보 불가/시간 초과·수신 후 영상
없음을 표시하며, 영상 없는 카메라도 선택할 수 있습니다. primary 재생과 접근 권한은 유지합니다.
pending 요청이 있으면 5초마다 새 상태를 조회하며 수동 새로고침도 제공합니다. 구버전 응답에
`clip_requests`가 없으면 기존 화면처럼 처리합니다.

자동 확정 preview는 pending/unavailable 모두 `evidence_incomplete`로 차단합니다.
다른 정상 자료가 있어도 누락 요청을 완결 증거로 바꾸지 않습니다. 운영 자동 확정은 OFF를 유지합니다.

## 검증 결과 (2026-11-15)

| 검사 | 명령/근거 | 결과 |
|---|---|---|
| 감독 단위 계약 | `python -m unittest inference_worker.test_multi -q` | 30개 통과 |
| 실제 OS 합성 자식 | `python -m unittest inference_worker.test_multi_processes -v` | 9개 통과, 각 종료 시 잔존 자식 0 |
| PG/API 누락 계약 | `python -m unittest deploy.tests.test_g3_evidence.G3EvidenceTests -v` | 1개 통과, OFF, 검토 쓰기 0 |
| UI TSX 로직 | frontend에서 `node scripts/mediaRecovery.check.cjs` | 10개 통과 |
| TypeScript | frontend에서 `node node_modules/typescript/bin/tsc -p tsconfig.app.json --incremental false` | 통과 |
| 관련 파일 lint | frontend에서 `node node_modules/eslint/bin/eslint.js src/components/EventMediaPanel.tsx src/api/events.ts` | 통과 |
| 제품 번들 | frontend에서 `node node_modules/vite/bin/vite.js build` | 통과 |

OS 자식 검사에는 복구 성공, 재실행 OSError, camera/reference 예산 소진, 정상 코드 0, backoff 중 stop,
fatal 설정 오류, 모든 카메라 종료, fail-fast가 포함됩니다. 카메라 소진 후 healthy 처리 카운터 34→59,
참고 수집기 소진 후 34→59, 재실행 실패 후 32→57을 확인했습니다. 원래 프로세스는 유지되고 해당
슬롯의 추가 재시도는 없었습니다. 검사 후 모든 직접 자식을 `wait`로 회수했음을 확인했습니다.
하네스가 남은 프로세스를 강제 청소하기 **전** 잔존이 0인지 검증합니다.

PG 검사는 담당자가 제공한 localhost:55437의 전용 `kyo_test_camera_recovery_20261115`에서
기존 isolated_api UUID 스키마로 실행했습니다. 기존 운영 이벤트를 사용하지 않았으며 끝난 뒤
`test_*` 스키마 0개를 확인하고 자원을 반납했습니다. ready_media는 합성 이미지 fixture이며 영상
완결성 또는 실제 위반 판단의 증거가 아닙니다. 초기 sandbox TCP 거절 후 승인된 격리 DB 접근으로 통과했습니다.

UI 검사는 실제 TSX에 hook/API 대역을 연결한 검사이며 실제 React DOM/브라우저 또는 영상 재생 검사는
아닙니다. 초기 검사 하네스의 import.meta/트리 탐색 문제는 수정 후 10개 모두 통과했습니다.
실제 카메라/제공 실영상/GPU, 네트워크 단절 실증, lease·원본 파일 정리 실증, 재시작을 넘는 동일 사건
연속성은 이 결과로 주장하지 않습니다. 현재 default run_server는 별도 FFmpeg 자식을 만들지 않으며,
continuous runner의 하위 encoder 트리 종료 검증은 이 테스트 범위가 아닙니다.

CI 설정은 변경하지 않았습니다. 감독 테스트는 기존 inference_worker unittest discovery에 자동 포함됩니다.
PG 전용 검사와 UI 스크립트는 위 명령으로 별도 수행했으며 통합 CI 편입은 조율 담당 범위입니다.
원격 main merge/자동 merge/배포/이슈 종료는 수행하지 않습니다. 최종 병합은 사용자 검토 사항입니다.
