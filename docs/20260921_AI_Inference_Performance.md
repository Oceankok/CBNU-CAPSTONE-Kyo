# AI 모델·실시간 추론 개선 가이드

## 확인된 원인

기존 실시간 코드는 프레임 전체의 `person` 수와 `helmet` 수를 비교했다. 여러 사람이 겹치면 어느 안전모가 어느 사람의 것인지 알 수 없어 오탐·미탐이 발생하고, 한 작업자의 상태 변화가 전체 후보 타이머를 초기화했다.

이벤트가 발생하면 같은 스레드에서 영상 인코딩, 썸네일/DB 저장, TTS까지 실행했다. 따라서 사람 수가 늘어 후보 이벤트가 자주 발생할수록 카메라 루프가 수 초 동안 멈출 수 있었다. 실제 오류 사례 19장에서는 2명 이상일 때 모델 추론 시간이 증가하지 않았으므로, 확인된 멈춤의 주원인은 이벤트 I/O 경로다.

데이터 변환에도 중요한 오류가 있었다. SHWD의 `head`는 안전모가 없는 머리 박스인데 `person`으로 변환되고 있었다. 그 결과 작은 머리 박스를 사람으로 학습하여 좁은 Person 박스, 중복 Person, Recall 저하를 만들 수 있다. 이제 `head -> no_helmet`으로 변환한다. 기존 병합 데이터는 반드시 다시 생성해야 한다.

## 현재 가중치 기준선

`Exp01_yolov8n_640_clean_3class-13/weights/best.pt`는 이름과 달리 설정 파일의 현재 4개 클래스가 아니라 `helmet`, `vest`, `person` 3개 클래스 모델이다. 따라서 명시적인 `no_helmet` 탐지는 불가능하고, 안전모와 매칭되지 않은 Person을 시간적으로 누적해 후보로 판단해야 한다.

학습 로그의 최고 전체 지표는 다음과 같다.

| 지표 | 최고값 |
|---|---:|
| Precision | 0.9037 |
| Recall | 0.8337 |
| mAP50 | 0.8937 |
| mAP50-95 | 0.6308 |

CPU에서 저장된 오류 사례 19장을 측정한 결과다. 실제 배포 장치와 실제 영상에서는 아래 벤치마크를 다시 실행해야 한다.

| 입력 크기 | 평균 latency | P95 latency | FPS |
|---:|---:|---:|---:|
| 416 | 33.3 ms | 39.2 ms | 30.1 |
| 512 | 47.2 ms | 55.5 ms | 21.2 |
| 640 | 72.2 ms | 89.6 ms | 13.9 |

오류 사례에서는 2명 이상 검출된 이미지의 추론 시간이 1명 이미지보다 커지지 않았다. 영상에서만 멈춘다면 이벤트 저장/TTS 시간, 카메라 버퍼 지연, 디스크 사용률을 우선 확인한다.

## 개선된 실시간 처리

`src/realtime_helmet_monitor.py`는 다음을 적용한다.

- ByteTrack ID를 우선 사용하고, 의존성이 없을 때 IoU 추적기로 자동 대체
- 사람별 머리 영역에 helmet/no_helmet을 일대일 공간 매칭
- 안전모 한 개가 두 작업자에게 중복 매칭되지 않도록 제한
- 작업자 ID마다 독립적인 시간 누적, 다수결 비율, cooldown 적용
- 영상 인코딩·DB 저장·TTS를 제한된 백그라운드 대기열에서 처리
- `imgsz`, `conf`, `iou`, `max_det`, 프레임 간격, tracker를 실행 옵션으로 제공
- 화면에 실제 capture FPS, 처리 FPS, 평균 inference latency 표시

현재 3클래스 모델은 안전모 미매칭을 미착용 후보로 추론한다. 4클래스 모델을 새로 학습한 뒤에는 더 보수적인 다음 옵션을 권장한다.

```bash
python src/realtime_helmet_monitor.py \
  --model runs/ppe/model_comparison/yolo11s_img640_seed42/weights/best.pt \
  --source 0 --imgsz 640 --conf 0.20 \
  --require-no-helmet-class
```

실제 영상으로 UI·DB·TTS 없이 순수 파이프라인을 먼저 확인하려면 다음과 같이 실행한다.

```bash
python src/realtime_helmet_monitor.py \
  --model Exp01_yolov8n_640_clean_3class-13/weights/best.pt \
  --source test_videos/site.mp4 --no-display --no-events
```

## Nano / Small / Medium 학습 비교

모델 외 조건을 동일하게 고정한다. 실시간 1-stream 추론은 batch 1이므로 학습 batch와 혼동하지 않는다. 학습은 `batch=-1`로 장치 메모리에 맞춰 자동 산정하고, 메모리 부족 시 정수 batch를 지정한다.

```bash
python scripts/data/convert_shwd_to_yolo.py
python scripts/data/merge_datasets.py
python scripts/data/split_yolo_dataset.py
python scripts/data/audit_yolo_dataset.py \
  --data configs/merged_ppe.yaml --output dataset_audit.json --strict

python scripts/train/train_ppe.py \
  --models yolo11n.pt yolo11s.pt yolo11m.pt \
  --imgsz 512 640 --epochs 100 --batch -1 --seed 42
```

비교 시에는 전체 mAP뿐 아니라 `person recall`, 클래스별 Recall, 작은/가려진 작업자, 2명 이상 장면을 별도로 본다. 권장 선택 순서는 다음과 같다.

1. 목표 장치에서 P95 latency와 FPS 기준을 통과하는 모델만 남긴다.
2. 남은 모델 중 Person Recall이 가장 높은 모델을 선택한다.
3. Small이 Nano보다 Recall을 유의미하게 높이고 P95 기준을 통과하면 Small을 우선한다.
4. Medium은 Small 대비 개선 폭이 배포 비용을 상쇄할 때만 사용한다.

## confidence와 실제 영상 벤치마크

confidence는 검증셋에서 자동 탐색한다. Person Recall이 중요한 안전 시스템이므로 0.10~0.40 범위를 먼저 확인하고, 오탐은 작업자별 2초 누적 로직으로 억제한다.

```bash
python scripts/tune_confidence.py \
  --model runs/ppe/model_comparison/yolo11s_img640_seed42/weights/best.pt \
  --data configs/merged_ppe.yaml \
  --output runs/ppe/confidence_sweep.csv
```

동일한 실제 영상에서 모델·해상도를 비교한다. 출력에는 평균/P50/P95 latency, FPS와 사람 수 0명·1명·2명 이상 구간의 latency가 포함된다.

```bash
python scripts/benchmark_inference.py \
  --models \
    runs/ppe/model_comparison/yolo11n_img640_seed42/weights/best.pt \
    runs/ppe/model_comparison/yolo11s_img640_seed42/weights/best.pt \
    runs/ppe/model_comparison/yolo11m_img640_seed42/weights/best.pt \
  --source test_videos/site.mp4 \
  --frames 300 --warmup 10 --imgsz 512 640 \
  --conf 0.20 --output runs/ppe/real_video_benchmark.csv
```

## 데이터 보완 체크리스트

- SHWD 변환 결과를 다시 만들고 `head`가 class 3(`no_helmet`)인지 표본 검사
- 기존 3클래스 가중치와 4클래스 YAML을 섞지 않기
- 원본 Construction-PPE의 train/val/test 분리를 유지해 데이터 누수 방지
- 원거리·부분 가림·역광·측면·밀집 작업자 장면을 test에 포함
- 2명, 3~5명, 6명 이상 구간별 Person Recall을 별도로 집계
- `person`은 전신/상반신 기준을 통일하고 머리만 있는 박스를 Person으로 라벨링하지 않기
- helmet/no_helmet 박스가 각 Person의 머리 영역과 함께 라벨됐는지 감사

## 완료 판정 권장 기준

- 목표 장치·실제 영상에서 P95 latency 100ms 이하 또는 요구 FPS 충족
- 이벤트 발생 직후에도 500ms 이상의 추론 정지가 없을 것
- 4명 이상 장면에서 ID와 PPE 귀속이 서로 바뀌지 않을 것
- 별도 test set에서 Person Recall 0.90 이상을 우선 목표로 설정
- Nano/Small/Medium 결과와 confidence 선택 근거를 CSV로 보관
