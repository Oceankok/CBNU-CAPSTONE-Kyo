# 연결된 수동 검토 사건·영상 수집 인계

이 후보는 PR #270의 `1cda197fc19917a55fdea6578a7c4e77fd7ca96f` 위에 구현했다. 기준 main은 `b0c0f1aec1caa0c24bbad6aea0c51c74a6169d23`이다. 현재 운영에는 적용하지 않았고 main 병합·PR 생성·CI 조회·배포·카메라/ROI/모델/DB 설정 변경은 하지 않았다. 변경 파일은 `inference_worker/**`뿐이다. Sender PR #268은 사용하지 않았다.

## 실제 실행 경로

현재 설정과 같은 legacy supervisor + review 명령이 기본적으로 연결된 수집 경로를 선택한다. `run_server --submit-review-candidates`는 기존 최초 qualification 클립 경로 대신 `run_continuous.run`의 manual configuration을 사용한다. `run_multi`는 선택된 각 카메라를 원본으로 하는 worker에 나머지 카메라를 참고 소스로 전달한다. 기존 legacy 참고 수집기는 이 모드에서 별도로 기동하지 않는다.

```powershell
python -m inference_worker.run_multi --runner legacy --camera-id LAPTOP_CAM_01 --camera-id LAPTOP_CAM_02 --model-path '<승인된 모델 절대경로>' --submit-review-candidates
```

이것은 검토 후 적용 시의 명령 계약이지 지금 운영에서 실행하라는 지시가 아니다. 기존 정상 노드 인증 환경을 사용하며 토큰을 인자로 넘기지 않는다. `--qualification-clips`는 과거 qualification 동작을 명시적으로 재현하는 호환 옵션이며 기본 경로가 아니다. strict verified continuous 설정 파일의 시각·증거 조건은 유지한다. manual-only 옵션을 strict profile로 주입하면 거절한다. 승인된 설치 shim에 남아 있는 “qualification-window clips only” 안내 문구는 향후 배포 담당이 새 기능에 맞게 별도로 검토해야 한다. 이 작업에서는 설치 파일을 수정하지 않았다.

## 동작

- 같은 camera/track/PPE의 이미 생성된 사건은 PPE 의무·정책 version·scope 기준·증거 모드가 같으면 ROI 소속 변경에도 같은 ID를 사용한다. 초기 qualification은 엄격한 기존 경계를 유지한다. 재착용, 실제 사람 이탈/새 ID, 알려진 의무 OFF→ON은 새 사건 경계다.
- 3초 qualification 후 start, 이후 단조 증가 progress, 종료 때 close를 영속 기록한다. 최초 qualification 시간만 고정하는 legacy HTTP 제출은 기본 검토 경로에서 사용하지 않는다.
- source UTC를 수정하지 않는다. manual은 `evidence_mode=manual_review`, `review_only=true`, confidence=null이다. 측정하지 않은 시각 오차를 0으로 보고하지 않는다. capture report는 `source_utc_unverified`, offset/uncertainty=null을 보낸다. 내부 0 오프셋은 원본 timestamp를 그대로 전달하는 연산일 뿐 측정값이 아니다.
- 미검증 카메라 간 시각을 동일하다고 강제하지 않는다. 각 카메라 내부 순서는 검사하되 manual mux가 참고 카메라를 기다리느라 원본을 멈추거나, 서로 다른 카메라 시각 순서 때문에 원본을 종료하지 않게 한다. 카메라 동기화가 검증됐다는 뜻은 아니다. 실제 참고 영상의 물리적 동시성은 미검증 상태로 남긴다.
- 72ms 경계나 일시적 GET 응답 유실은 추적/사건 상태를 초기화하지 않는다. 수신 지연은 host monotonic arrival로 감시하고 미검증 source UTC와 host UTC를 비교해 시계 정확도를 꾸미지 않는다. 첫 물리 스트림이 없을 때는 정상 대기하며 중지 파일을 따른다.
- 인코딩 자료만 durable spool에 저장하고 raw 프레임은 제한된 prebuffer/한 입력 샘플로 유지한다. 기본 encoded segment 상한은 30초, 카메라별 사건 spool 32MiB/512조각, 전체 예약 1GiB, 보존 상한 1시간이다. 메모리/장부/업로드에도 한도가 있으며 초과·만료는 누락으로 기록한다.
- 원본·참고는 같은 사건의 요구 구간을 사용하되 실제로 확보된 영상의 camera/role·구간을 유지한다. 앞부분/내부 공백/뒷부분 부족 때문에 전체 영상을 버리지 않는다. 누락은 `missing_pre_roll`, `source_input_gap`, `missing_post_roll`로 보고한다. 하나의 출력 프레임보다 작은 끝부분을 억지로 늘리지 않고 실제 인코딩 grid에서 닫은 잔여 부분도 누락으로 기록한다.
- start/update, per-camera manifest, close, capture report, segment upload는 같은 사건·원래 세션·불변 요청 body/조각 ID로 재시도한다. 응답 유실 후 서버 receipt를 확인하고 정리한다. 진행 중 중단은 마지막 영속 관측까지 불완전 종료하며 이미 완성된 조각을 먼저 등록·복구한다. `.part`와 사람 추적 상태를 복원하거나 관측하지 않은 시간을 만들지는 않는다. 모든 정리에는 기존 원본 소유권/receipt 검사를 적용한다.

## 필요한 백엔드 계약

기존 `/api/processing/continuous-events` start/updates/segments/capture-reports/receipts를 사용한다. 서버 후보에는 `evidence_mode=manual_review`, 미검증 시각과 null clock 값, 부분 범위 보존 및 수동 검토 의미가 필요하다. 카메라별 manifest는 `camera_id`, 카메라별 0부터의 sequence, 실제 start/end를 보낸다. 카메라 간 조각 경계가 같다고 가정하지 않는다. close의 `final_segment_count`는 원본 카메라 manifest 수다. 원래 camera/context/zone을 옮기거나 사람을 시간만으로 합치지 않는다.

별도 backend 후보와 실제 격리 API로 연결 검증했다. 정확히 사용한 backend 파일 SHA256은 `.private_media/connected-validation/api-contract-result.json` 및 `api-active-recovery-result.json`에 남겼다. backend 쪽 당시 HEAD는 b0이고 해당 소유자의 미커밋 수정 파일을 조합했으므로, HEAD만으로 조합 검증 결과를 표현하면 안 된다. backend 최종 커밋과 함께 검토해야 한다.

## 검증 결과

모든 영상·사람/PPE 탐지는 합성이다. 실제 MP4 청크 디코더, ByteTrack, H264 인코더, spool/outbox/dispatcher 및 별도의 실제 격리 FastAPI+임시 SQLite를 사용했다. 카메라·GPU 모델·운영 API·운영 DB·유료 호출은 사용하지 않았다.

| 확인 | 결과 |
|---|---|
| 실제 기본 runner에 26개의 1초 MP4 입력, 약 20초 미착용 | 원본 PPE 사건 1개, progress와 최종 close, 원본·참고 영상 유지 |
| 공백 없는 기본 설정 출력 | 카메라별 H264 단일 파일 약 21.933초. 시작 전 영상은 없으므로 pre-roll 누락을 표시 |
| 72ms seam + 일시적 source GET ReadTimeout | 같은 사건 ID 유지, 카메라별 10초/11.933초 조각, 실제 공백과 끝 grid 잔여를 누락으로 표시 |
| 내부 누락·앞부분/뒷부분 누락 | 확보된 영상은 보존하고 누락 범위를 분리 보고 |
| 재착용 후 다시 미착용 | 별도 두 사건 |
| 응답 유실 후 장부 재개방 | 같은 ID/조각으로 복구, 중복 업로드 없음 |
| 실제 격리 API: close 이후 응답 유실 | 같은 원본 사건/원본·참고 역할, private 업로드·receipt·정리·완료 이력 판별 통과 |
| 실제 격리 API: 진행 중 중단 | 완성된 조각을 원래 세션으로 등록·불완전 종료·업로드 복구. 종료 시각은 마지막 영속 progress와 같으며 중단 직전 미기록 관측을 더하지 않음 |
| 개인정보 경계 | 실제 API 업로드는 processing queue 상태, public URL=null. 얼굴 가림 모델/공개 브라우저 재생 통과로 확대하지 않음 |

관련 회귀 묶음은 167개 실행, 166개 통과/Windows symlink 권한 검사 1개 skip이었다. 잘못 지정한 존재하지 않는 `test_run_multi` 모듈은 목록 확인 후 실제 `test_multi`로 수정했다. supervisor의 기존 “참고 수집기 1개” 기대는 새 기본 검토 경로에 맞게 수정했으며 31개 unit 검사가 통과했다. 이후 변경 범위에 맞춰 runner 15개, journal 포함 25개, recovery/outbox 48개(47통과/같은 symlink 1 skip), 실제 API 2개를 확인했다. 이 수는 서로 중복되는 묶음이므로 합산하지 않는다. 마지막 기본 runner 3개도 통과했다.

초기 격리 API 실행은 Windows 샌드박스의 asyncio socketpair 초기화에서 대기했다. 스택으로 원인을 확인해 해당 시험 프로세스만 종료한 뒤 정상 실행 권한으로 임시 SQLite/in-process API 시험을 수행했다. 서버 서비스는 조작하지 않았다. 인코더 끝 grid 불일치, 부분 조각 복구의 이전 session 등록 guard, 최초 진행 중 중단 시 마지막 영속 progress와의 차이도 실제 실패를 확인한 뒤 수정/검증했다. 실패한 실행을 통과로 세지 않았다.

## 재현

FFmpeg/ffprobe를 PATH에 두고 tracking dependencies를 준비한다. CI/운영을 실행하는 명령이 아니다.

```powershell
python -B -m unittest inference_worker.test_configured_manual_runner inference_worker.test_connected_manual_incident -q
python -B -m unittest inference_worker.test_session_recovery inference_worker.test_settled_history inference_worker.test_capture_outbox -q
$env:PPE_CONNECTED_BACKEND_SOURCE='<backend 후보 작업 폴더>'
python -B -W ignore -m unittest inference_worker.test_connected_manual_api -q
```

API 조합 검사는 해당 모듈만 별도 Python 프로세스로 실행한다. `PPE_CONNECTED_VALIDATION_OUTPUT`을 작업 폴더 내 경로로 설정하면 합성 인코딩 영상과 JSON을 보존한다. 이번 결과는 `.private_media/connected-validation/`에 있다. `ed320b8fa14147ee9844df471ef4d4e9/`는 공백 없는 단일 긴 영상, `a86a958a68fa47918aefb9d394967af9/`는 72ms seam 영상이다. 각 폴더의 ffprobe.json은 H264·크기·실제 길이를 확인한다. 디코더 출력과 인코딩 길이는 API 사건 구간과 따로 기록했다.

## 적용 전 남은 확인

backend 최종 후보와의 통합 검토, 실제 카메라/학습 모델, 얼굴 가림 처리 완료와 브라우저의 여러 조각 재생은 별도다. 특히 30초를 넘는 사건이나 실제 공백은 여러 조각이므로 UI가 이를 전체 사건의 일부로 명확히 제시해야 한다. 과거 다섯 사건의 사라진 영상은 이번 시험으로 복구됐다고 주장하지 않는다. 장기 가림/사람 ID 교체, 프로세스 재시작을 넘는 동일인 추적은 보장하지 않는다. 병합·배포는 사용자 보고/검토 후 별도 승인 범위다.
