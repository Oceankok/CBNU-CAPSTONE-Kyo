# PPE 사건 생성 안정화 인계 — 2026-11-15

## 범위와 반영 위치

작업 브랜치는 `codex/ppe-episode-stability-20261115`이며 기준은 main `ac9d254774ae5c1d0ebdc9019f249b7ddceb68e8`입니다.
기존 `codex/event-episode-lifecycle-20261113` 작업 폴더의 미커밋 실험은 보존했습니다.

사람 추적, 사람–보호구 연결, 같은 카메라 사건의 시작·유지·종료, 메타데이터 저장 재시도를 수정합니다.
자동 확정 모델/어댑터, backend 판정·통계, 프론트, 다른 카메라 사건 병합, 사람 탐지 모델 재학습은 포함하지 않습니다.
실제 서비스 배포·운영 데이터 변경·PR 병합·이슈 종료는 수행하지 않았습니다.

기본 `run_multi → run_server` 경로가 새 구현을 사용합니다. `run_multi.py`는 변경하지 않습니다.
`run_local/run_chunks/scenario_collector/realtime_helmet_monitor`도 실제 프레임 시각을 추적기에 전달합니다.
옵션 continuous 경로는 같은 추적·PPE·EventLifecycle을 사용하지만 기존 수집·전송 프로토콜을 유지합니다.

## 구현 계약

| 항목 | 동작 |
|---|---|
| 추적 | Ultralytics ByteTrack, 카메라마다 독립 ID 카운터, 실제 source timestamp로 이탈 만료 |
| 약한 사람 탐지 | 점수 0.1 이상을 기존 ID 연결에 사용합니다. 새 ID/보호구 미착용 근거에는 0.5 이상이 필요합니다. 약한 사람 박스는 보호구 소유자·가림 판정 경쟁에도 참여하지 않습니다. |
| 보호구 연결 | 머리/몸통 영역과 위치 점수로 전역 일대일 연결합니다. 경합·겹침·관측 불가를 unknown으로 보존합니다. 머리 영역은 사람 박스 위쪽까지 확장합니다. |
| 신원 경합 | 강한 사람 박스가 크게 겹치면 해당 프레임의 PPE 판정을 보류합니다. 외형 ReID는 없으므로 완전 겹침 후 신원을 보장하지 않습니다. 서로 다른 ID의 사건을 사후 자동 병합하지 않습니다. |
| 최초 후보 | 최대 6초 관측창에서 유효한 미착용 관측 간격 합계 3초가 필요합니다. 연속 샘플 최대 간격은 0.5초입니다. unknown/누락 구간을 관측량에 더하지 않습니다. |
| 후보 전 unknown | 마지막 유효 미착용부터 0.5초까지 잠시 보류합니다. 초과 또는 관측창 초과이면 새 관측을 시작합니다. |
| 진행 중 사건 | 동일 사람이 실제 탐지되어 보이면 PPE unknown이 길어져도 사건 ID를 유지합니다. 위치·정책 범위를 해석하지 못한 프레임도 unknown으로 처리하고 마지막으로 확인된 범위를 보존합니다. 그 구간은 관측량에서 제외합니다. 실제로 확인된 영역·정책 버전·필수 PPE 변경은 기존처럼 사건을 분리합니다. |
| 사람 이탈 | 실제 사람 관측이 끊긴 뒤 1초 유예를 넘으면 종료합니다. 장기 이탈·프로세스/소스/정책 변경을 넘어 신원을 복원하지 않습니다. |
| 재착용 | PPE-positive가 0.5초 연속 관측되어야 종료합니다. 한 프레임 착용 오탐으로 사건을 재설정하지 않습니다. 이후 탈착은 새 사건 ID를 생성합니다. |
| 독립성 | 카메라·tracking_id·PPE·정책 범위가 다른 사건은 별도로 관리합니다. |
| 저장 | 후보 시점 event_id와 요청 body를 SQLite 메타데이터 outbox에 먼저 기록합니다. 같은 프레임의 모든 후보를 기록한 뒤 HTTP를 호출합니다. |
| 재시도 | 응답 유실/429/500/502/503/504/비정상 JSON은 같은 body로 지수 지연 재시도합니다(1~30초). 409/422 등 영구 거절은 rejected로 보존하고 자동 반복하지 않습니다. 401/403은 요청을 보존하고 실행을 중지합니다. |
| 용량 | 요청 256개, 각 body 최대 64KiB입니다. 가득 차면 기존 항목을 지우지 않고 명시적 오류로 중지합니다. |
| 영상 | 시작 전 영상 부족이 후보 생성 자체를 막지 않습니다. 불완전 영상은 제출하지 않고 해당 clip request의 unavailable 보고를 시도합니다. 영상 저장·재연결 자체를 이 outbox가 복구하는 것은 아닙니다. |
| 리뷰 전용 | 3-class 모델은 계속 review_only=true, confidence=null입니다. 사람 점수나 보호구 부재로 미착용 확률을 만들지 않습니다. |

위 수치는 이 변경의 초기 설정이며 일반 환경에서 검증된 정확도·운영 최적값을 뜻하지 않습니다.
확인된 정책 범위 변경으로 종료할 때는 로컬 close-intent에 이전·다음 범위의 SHA-256, 버전·영역·필수 PPE 변경 여부와 해당 관측 시각을 기록합니다. 이 고정 크기 진단은 HTTP 요청에 추가하지 않으며 좌표나 원문 범위 값을 복제하지 않습니다.
`run_server`는 `--episode-missing-grace-seconds`, `--rewear-seconds`, `--qualification-grace-seconds`를 받습니다.
continuous의 `lifecycle` 설정은 CLI 재착용/qualification 기본값을 덮어쓸 수 있습니다. 이 경로의 missing_grace는 기존 명시적 설정값을 사용합니다.
직접 `EventLifecycle/LifecycleJournal`을 만드는 기존 호출자의 재착용/qualification 기본값 0은 유지했으며 실제 runner가 새 값을 전달합니다.

저장 디렉터리는 `--event-outbox-directory` 또는 `PPE_EVENT_OUTBOX_DIR`, 기본 `.private_media/event-outbox`입니다.
서버 주소와 카메라별 파일을 사용합니다. 재시작 복구에는 같은 디렉터리가 필요합니다.
메타데이터만 영속화하며 이미지·클립·사람 신원·진행 중 사건 전체 상태는 복원하지 않습니다.
rejected 항목의 임의 삭제나 ID 변경 재전송은 구현하지 않았습니다. 원인 확인 후 별도 복구 절차가 필요합니다.

## legacy와 continuous, 자동 확정 담당 인계

legacy HTTP 이벤트는 최초 qualification의 시작·끝을 고정합니다. 전체 지속 사건 종료 시각으로 갱신하지 않습니다.
따라서 lifecycle/collection이 없는 legacy 후보에 continuous 자동 확정 규칙을 적용하면 안 됩니다.

continuous 전이는 다음 의미를 유지합니다.

- event_id는 사건당 고정됩니다. end_time은 마지막 유효 미착용 관측입니다.
- observed_seconds는 인접한 유효 absence 샘플 간격 합계입니다.
- gap_count/gap_seconds는 미착용이 다시 관측될 때 앞선 마지막 absence와의 공백을 기록합니다.
- 마지막 absence 이후 unknown/사람 누락/샘플 공백이 있고 absence가 재개되지 않았다면, 정상 종료 3종에 `_after_unknown`을 붙입니다: `ppe_present_after_unknown`, `track_departed_after_unknown`, `primary_source_ended_after_unknown`.
- 후행 unknown 시간을 사건 span 밖의 gap_seconds에 억지로 추가하지 않습니다. 진단 전이에는 trailing_unknown도 남으며 wire 계약에는 close_reason으로 전달합니다.
- 정상적인 연속 PPE-positive 재착용 확인 시간은 unknown이 아닙니다.
- observation_gap·정책/소스 변경 등 기존 비정상 종료는 그대로 검토 대상입니다.

자동 확정 담당과 위 계약을 합의했습니다. 현재 규칙 allowlist에서 `*_after_unknown`은 제외됩니다.
이 PR은 자동 확정을 활성화하거나 규칙을 완화하지 않습니다.

## 원본 영상 수동 표본 관찰

추론 결과를 정답으로 삼지 않고 원본 프레임의 사람·착용 상태를 먼저 확인했습니다.
아래 구간은 표본 프레임을 바탕으로 하므로 탈착 경계에 약 0.5초 불확실성이 있습니다.
동일한 복장의 한 사람이 들어와 장비를 벗고 나가는 장면이며, 파일 간 절대 UTC 동기화는 검증하지 않았습니다.

| 파일 | 구간(파일 시작 기준 초) | 수동 관찰 |
|---|---:|---|
| Camera01 | 0~12.5 | 빈방, 사람 없음 |
| Camera01 | 13~14 | 우측 가장자리 진입, 부분 관측 |
| Camera01 | 14~26 | 안전모·조끼 착용, 머리/몸이 프레임 경계에 걸리는 구간 존재 |
| Camera01 | 26~26.5 | 안전모를 벗는 전환 |
| Camera01 | 26.5~39 | 안전모 미착용, 조끼 착용 |
| Camera01 | 39~43 | 조끼 탈착 전환 |
| Camera01 | 43~55 | 안전모·조끼 미착용, 손에 장비를 들고 이동 |
| Camera01 | 55~56 | 우측 퇴장, 부분 관측 후 빈방 |
| Camera02 | 0~13 | 빈방, 사람 없음 |
| Camera02 | 13.5~14 | 우측 진입, 부분 관측 |
| Camera02 | 14~25.5 | 안전모·조끼 착용 |
| Camera02 | 25.5~26 | 안전모 탈착 전환 |
| Camera02 | 26~38 | 안전모 미착용, 조끼 착용 |
| Camera02 | 38~42 | 조끼 탈착 전환 |
| Camera02 | 42~54 | 안전모·조끼 미착용 |
| Camera02 | 54~55 | 우측 퇴장 후 빈방 |

두 영상에 실제 재착용 후 재탈착이나 여러 사람이 교차하는 시나리오는 없습니다.
이 기준은 합성 회귀로 확인했으며 추가 실영상 검증이 필요합니다.

## 같은 탐지열로 비교한 결과

모델은 기존 `best.pt`, SHA256 `31037870bc9d677c679ad41ab24a65f7662f8d67bef54040b0b8cb37901c0d75`입니다.
Ultralytics 8.4.171 / cuda:0으로 Camera01 975프레임(65초), Camera02 960프레임(64초), 총 1,935프레임을 실제 추론했습니다.
두 파일 모두 15fps입니다. 원본 영상과 가중치는 변경하지 않았습니다.

한 번 생성한 탐지 캐시에서 baseline은 0.5 이상만 사용했고, 수정본은 같은 강한 탐지만 사용한 대조군과 0.1 이상 사람을 추적에 추가한 군을 각각 실행했습니다.
PPE 탐지 기준 0.5, 카메라 전체 helmet 정책과 별도 helmet+vest 정책을 동일하게 적용했습니다.
native FPS의 frame_index/fps가 시간 기준이며, 운영 스트림 분할·네트워크·UI·실제 미디어 제출은 이 비교에 포함하지 않습니다.

| 평가 대상 | main | 수정본 |
|---|---|---|
| Camera02 실제 사람의 연속 안전모 미착용 | 28.800 / 38.133 / 50.200초, track-6/8/11의 3건 | 28.800초, track-4의 1건 |
| Camera01 실제 사람의 안전모 미착용 | 34.067초 1건 | 34.067초 1건, 지연 잔존 |
| Camera02 실제 조끼 미착용(helmet+vest 정책 별도) | 50.200초 | 44.200초 |
| Camera01 실제 조끼 미착용(helmet+vest 정책 별도) | 0건 | 0건, 관측 불가/잘림 한계 잔존 |

빈방 사람 오탐 후보도 별도로 남았습니다. Camera02는 main 12.467초, 수정본 11.200초에 1건입니다.
Camera01은 main 3.000/6.467/62.667초, 수정본 3.000/58.267초에 빈방에 해당하는 안전모 후보가 있습니다.
따라서 사람 탐지 오탐이 해결됐다고 주장하지 않습니다. 특히 과거 12.5초 후보는 이번 파일 대조에서는 Camera02 빈방 시점에 대응합니다.
과거 28.9/38.2/50.3과 이번 결과의 약 0.1초 차이는 재생/샘플 시각 기준 차이 가능성이 있으며 동일한 실행 로그라고 단정하지 않습니다.

낮은 점수 사람 박스를 연결·가림 경쟁에서 제외한 뒤, 강한 탐지만 사용하는 수정본과 약한 추적 보조를 포함한 수정본의 위 사건 시각은 같았습니다.
최종 lifecycle의 실제 사람 안전모 사건은 Camera01 관측 10.733초/공백 19회 12.933초, Camera02 관측 26.267초/공백 12회 1.667초이며 둘 다 observation_gap으로 끝납니다.
이 값은 자동 확정 가능한 완전 관측 사건이라는 뜻이 아닙니다.

탐지 캐시·상세 프레임 평가·전이·summary·수동 확인용 이미지와 로그는 해당 worktree의 `.private_media/revalidation`에 보관했습니다.
원본 영상/이미지/탐지 캐시는 PR에 올리지 않습니다. 간략한 기계 판독 결과는 같은 디렉터리의 replay summary JSON 및 커밋된 validation JSON에 있습니다.
diagnostic event_id는 이 재생 실행에서만 생성됐으며 운영 서버에 제출하지 않았습니다.

## 검증과 재현

추론 전체 테스트는 전용 PostgreSQL DB, 번들 FFmpeg, 모델을 로드하지 않는 합성 fixture로 실행합니다.
Windows의 원본 디코더/심볼릭 링크 실패 8개는 변경 전 main에서도 동일하게 재현했습니다.
원본 파일 binary write/identity 검사 및 symlink 권한 관련 문제이며 이 PR 소유 파일이 아닙니다.
해당 실패를 건너뛰어 전체 통과로 표시하지 않습니다. 최종 실행 수와 명령은 validation JSON을 확인합니다.

의미 있는 추가 회귀는 같은 사건 장기 유지, 짧은 착용 오탐, 확인된 재착용 후 새 사건, PPE별 독립성,
관측량에서 unknown 제외, 사람이 보이는 장기 unknown, 후행 unknown 종료,
약한 탐지의 ID 유지·PPE 배제, 큰 겹침 시 판정 보류, 카메라별 ID 독립성,
응답 유실·프로세스 재시작·422 보존·401/403 중단·큐 포화·변조/다른 body 거부,
같은 프레임 복수 사건 저장 후 lease 오류, poll 중단 후 상태 유지입니다.
실제 PostgreSQL API의 commit 뒤 응답 유실을 재현하여 동일 이벤트 1건과 pending/review_only 보존을 확인합니다.

설치:

```powershell
python -m pip install -r requirements.txt
```

전체 추론 검사(운영 DB를 사용하지 않습니다):

```powershell
$env:PPE_TEST_POSTGRES_URL='postgresql://kyo_test@127.0.0.1:55437/kyo_test_ppe_episode_20261115'
python -X utf8 -B -m unittest discover -s inference_worker -t . -v
```

DB의 이름은 kyo_test로 시작해야 하며 fixture마다 별도 schema를 생성·삭제합니다.
위 주소는 이 세션의 공유 로컬 테스트 클러스터 예시이며 인스턴스 시작/정지는 조율 담당 소유입니다.
FFmpeg 실행 파일이 PATH에 있어야 영상 합성 검사가 실행됩니다.
이번 로컬 lap/ffmpeg 보조 파일은 .test-deps에만 설치했고 운영 Python 설정은 바꾸지 않았습니다.

영상 비교 재현:

```powershell
python -X utf8 -B -m inference_worker.replay_episode_comparison --video 'C:/Users/robin/Downloads/Kyo-Final-20261113-Helmet-Camera01.mp4' --video 'C:/Users/robin/Downloads/Kyo-Final-20261113-Helmet-Camera02.mp4' --model 'C:/Users/robin/Projects/CBNU-CAPSTONE-Kyo/best.pt' --baseline-ref ac9d254774ae5c1d0ebdc9019f249b7ddceb68e8 --output .private_media/revalidation
```

같은 모델·파일 해시가 있으면 기존 탐지 캐시를 재사용합니다. 결과 event_id는 재실행마다 달라집니다.
실제 모델/사람 데이터로 일반화 성능을 주장하려면 별도 다인·가림·재착용 자료가 필요합니다.

## 다음 담당 경계

자동 확정 담당: legacy 제외, continuous gap 및 _after_unknown 종료 보존, 실서버 collection/media 완료 증거는 별도 검증합니다.
현재 replay는 collection_complete=false / media_delivery_tested=false입니다.

장애 격리 담당: run_multi의 자식 프로세스 재시작은 메타데이터 재시도 복구와 별개입니다.
같은 outbox 디렉터리를 재사용하면 저장 요청은 복원되지만 tracker/active episode는 복원되지 않습니다.
장기 소스 중단·프로세스 재시작을 넘어 동일 사람 사건 1건을 보장하지 않습니다.

통합 담당: CI의 CPU tracking 의존성만 이 PR에 포함합니다.
교육 프론트 브랜치 통합 뒤 별도로 Playwright Chromium 설치와 test:education:browser를 추가해야 합니다.
Windows 디코더 8개 기존 실패, 실제 다인/재착용 장면, Camera01 잘림 지연·조끼 미생성,
실서비스 clip 연결/표시, 장기 중단·재시작 사건 연속성은 확인 대기입니다.
