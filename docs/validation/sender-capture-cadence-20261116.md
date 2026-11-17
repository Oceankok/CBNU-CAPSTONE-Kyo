# 송신기 early-read 누락 수정 후보

기준은 main `bd29d8f70dd0a4b49fc4703cc9da2586b96b4f08`이다. 격리 worktree에서만 수정했으며 운영 서버·노트북·공용 main에는 적용하지 않았다. 추가 시각 측정이나 촬영을 요청하지 않았다.

기존 실제 capture loop를 가짜 카메라·시계로 실행하면, 성공한 read가 예약보다 2ms 이르다는 이유로 버려져 15프레임 제출에 16회 read가 발생했다. 수정 전 회귀 시험은 `16 != 15`로 실패했다. 이는 로컬 합성 재현이며 사용자가 보고한 실제 trace의 16회/15회·약 2ms/80ms 수치를 이 PC에서 독립 확인했다는 뜻은 아니다.

단순히 read 전에 기다리는 방법은 15fps 요청을 무시하는 30fps FIFO 장치에서 오래된 프레임을 쌓는다. 합성 반례에서 15번째 소비 시점의 지연이 0.4초를 넘었다. 따라서 카메라 reader와 encoder 소비 사이에 최신 프레임 슬롯 하나만 둔다.

- reader가 read 완료 관측 UTC·monotonic을 프레임과 저장한다. 센서 노출 시각이라고 주장하지 않는다.
- consumer는 기존 15fps 예약 시점에 최신 슬롯을 한 번만 소비한다. 이른 프레임은 시각 비교만으로 버리지 않는다. 더 빠른 장치에서 새 프레임이 이전 미소비 프레임을 대체하는 경우는 `superseded`로 명시하는 downsampling이다. 프레임 복제나 무제한 큐는 없다.
- blocking 15fps 입력에는 read 지연을 프레임마다 추가하는 sleep을 넣지 않는다. instant/broken backend의 CPU busy loop만 reader의 1ms 협력 대기로 제한한다. consumer는 condition에서 기다리고 stop을 최대 20ms 간격으로 확인한다.
- 느린 read/write는 실제 관측 시각으로 남는다. `이전 timestamp+duration`이나 CFR 미래 시각으로 보정하지 않는다. 기존 encoder 옵션, 청크 번호, 보관/전송 규칙, 완료 ACK 조건, 서버 20ms·clock 조건을 변경하지 않는다.
- camera read와 release는 reader 소유다. 정상 종료는 reader join 후에만 완료 증거를 발행한다. 2초 내 join이 확인되지 않으면 encoder를 정리하더라도 정상 capture-result를 발행하지 않는다. 다른 thread에서 release하면 막힌 read가 풀린다고 가정하지 않는다.

새 focused 시험 11개가 통과했다. early read 보존, 15fps blocking 입력의 누적 지연 방지, 실제 30fps FIFO 입력의 최신 프레임 유지, 느린 read의 실제 UTC 유지, 느린 write의 중복 catch-up 방지, stop 응답, read 실패, reader shutdown 미확정 시 완료 증거 금지 등을 포함한다. 기존 capture sequence/context sender/starter 시험 38개도 통과했다. 총 49개 합성·단위 시험이며 실제 장치 검증은 아니다. 느린 처리나 장치 자체의 실제 시간 공백까지 없어진다는 주장은 하지 않는다.

## custom 노트북 기능과의 경계

현재 tracked main의 capture.py에는 replay 경로가 없다. 실제 노트북에는 별도 replay/retain/retry 코드와 관측 패치가 존재하므로 main 파일로 통째로 덮으면 안 된다. 실제 custom 전체 파일도 이 worktree에는 없다.

custom 적용은 별도 검토 대상이다. live만 최신 슬롯 reader를 사용하고, replay는 기존 동기 read·EOF·원본 순서 경로를 그대로 유지해야 한다. 기존 archive publication과 complete manifest, resume/retry 제약도 보존해야 한다. 현재 변경은 main 후보로만 검증했으며 custom 전체 파일에 대한 적용·시험이나 설치 패키지 완성을 주장하지 않는다.
