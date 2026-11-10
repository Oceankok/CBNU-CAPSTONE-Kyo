# RetinaFace 보조 얼굴 검사

SCRFD 처리를 유지하면서 RetinaFace ResNet50의 결과를 추가하는 선택 기능이다.
얼굴 인식/신원 식별/임베딩을 사용하지 않으며 영상과 사진은 호스트 안에서 처리한다.

## 동작

1. 기존 탐지 프레임에서 SCRFD 또는 YuNet과 RetinaFace의 얼굴 영역을 합친다.
2. 기존 모자이크와 영상 추적을 수행한다.
3. 최종 JPEG 또는 H.264 MP4를 다시 디코딩해 검사한다. 영상은 모든 프레임을 검사한다.
4. 검출된 잔여 얼굴 의심 영역에 50% 여유를 더해 검은 불투명 마스크를 적용하고 재인코딩한다.
5. 추가 처리는 최대 두 번이다. 여전히 검출되거나 검사/디코딩/인코딩에 실패하면
   함수가 오류를 반환하므로 기존 서비스와 worker는 결과를 공개하지 않는다.

검출된 얼굴이 없다는 것은 모델 검사 통과이며 익명성 보장이 아니다. 검출 누락과 오탐이
가능하다. 실제 작은 얼굴·어두운 장면·옆얼굴·가림 장면의 비교 평가가 필요하다.

## 가중치 계약

출처: <https://github.com/biubug6/Pytorch_Retinaface>

위 프로젝트의 ResNet50 테스트 모드 ONNX 내보내기 계약을 따른다.
입력은 float32 NCHW `[1,3,640,640]`, BGR 평균 `(104,117,123)`을 뺀 값이다.
출력은 후처리 전 `[1,16800,4]` 위치, `[1,16800,2]` 배경/얼굴 확률,
`[1,16800,10]` 랜드마크이다. 출력 이름/순서와 무관하게 마지막 차원으로 식별한다.
다른 얼굴 모델이나 후처리 포함 ONNX를 파일 이름만 바꿔 사용하면 안 된다.
일반 PPE용 `best.pt`와도 별개이다.

외부 코드나 pickle 체크포인트를 자동 실행/다운로드하지 않는다. 신뢰한 원본 가중치를
별도 환경에서 위 계약으로 내보낸 단일 ONNX를 준비한다. 원 출처와 가중치 이용 조건을
확인해야 한다. upstream 구현 코드는 MIT이며 사용한 가중치의 조건도 별도로 확인한다.

```powershell
python -m backend.media.setup_retinaface --source C:\ReviewedModels\retinaface.onnx --sha256 <검토한-내보내기-파일의-SHA256>
```

등록은 파일과 manifest를 설치하며 추론하지 않는다. 로컬 체크섬은 파일 변조 확인용이며
배포자나 ResNet50 백본 자체를 독립적으로 인증하지 않는다. 외부 데이터 파일이 필요한
분할 ONNX가 아닌 단일 파일 내보내기를 사용한다.

GPU worker의 실행 환경에 다음을 설정하고 worker를 재시작한다.

```powershell
$env:PPE_MEDIA_FACE_VERIFIER = 'retinaface'
# 기본 경로를 사용하지 않는 경우:
$env:PPE_RETINAFACE_MODEL_PATH = 'C:\ReviewedModels\retinaface_resnet50_640.onnx'
```

경로를 변경하면 해당 경로 옆에 등록 manifest도 있어야 한다. 같은 환경이 원격 worker와
로컬 처리/스냅샷/사진 처리에 적용된다. Docker API가 아니라 GPU를 가진 호스트 worker에
설정한다. `off`가 기본값이며, 비활성 상태를 검사 통과로 보고하지 않는다.
`retinaface`로 활성화했는데 모델·체크섬·CUDA·출력 계약이 잘못되면 원본이나 기존
미검사 결과로 우회하지 않고 해당 작업이 실패한다.

## 상태와 남은 작업

기존 ready/failed 공개 차단과 재처리 API를 사용한다. worker 실패 코드에는
`face_verification_failed`, `face_verifier_model_invalid`,
`face_verifier_configuration_invalid`가 추가됐다. 검사 통계는 로컬 처리 profile에 포함된다.
현재 API의 ready만으로 보조 검사 활성 여부를 판단하면 안 된다.

스키마 v20은 검사 기록(`verification_json`)과 원본 삭제 시각(`source_deleted_at`)을 추가한다.
worker는 검사 모델·통과 상태·횟수·프레임 수·최종 결과 SHA256을 서버에 전달한다.
서버는 프레임 수와 업로드 결과 해시를 대조해 기록한다. 이 기록은 인증된 worker의 보고이며
서버가 직접 얼굴 검사를 다시 수행한 증명이나 악성 worker에 대한 암호학적 증명은 아니다.

API의 각 미디어에 `face_verification`과 `source_deleted_at`을 제공한다. 기존 자료는
`not_run`이고 검사 통과로 소급 처리하지 않는다. 반복 실패는 `failed`,
`action_required=true`, `available_actions=[retry,delete]`를 제공한다.
**실패 결과와 원본은 관리자에게 미리보기로 제공하지 않는다.** 기존 관리자 재처리/삭제
흐름을 쓸 수 있으며 새 상태를 친절하게 보여주는 UI는 프론트 후속 작업이다.

서버의 선택 정책:

- `PPE_MEDIA_REQUIRE_FACE_VERIFICATION=true`: 검사 보고가 없는 새 결과 공개를 차단한다.
- `PPE_MEDIA_DELETE_VERIFIED_SOURCE=true`: 위 공개 제한도 적용하고, 성공한 결과의 worker
  임시 파일 정리가 확인된 뒤 서버 원본을 삭제한다. 실패한 원본은 자동 성공 처리하지 않는다.
- 두 정책 중 하나가 활성화되면 새 원본의 기본 보관 한도는 1시간이다.
  `PPE_MEDIA_SOURCE_TTL_SECONDS`로 명시한 값이 있으면 그 값을 우선한다.

두 정책의 기본값은 false로 기존 worker와의 배포 전환을 지원한다. 실제 worker 검사기를
먼저 준비한 후 서버 정책을 활성화한다. 삭제 실패 시 원본 경로와 검사 기록을 유지하고
주기 정리에서 재시도한다. 만료·관리자 삭제 정책은 기존처럼 적용하며, 기존 자료의 보관
기한을 일괄 변경하거나 검사하지 않은 원본을 조기 삭제하지 않는다. DB/미디어 백업의 보관
기한은 별도 운영 정책이므로 이 기능이 과거 백업에서 원본을 제거하는 것은 아니다.

실제 CUDA 모델 호환성·누락 보완 정도·처리 시간은 가중치 등록 후 검증해야 한다.
PPE 추론 우선권을 기존 inference guard를 통해 유지한다. 단일 프레임 모델 실행 중에는
즉시 선점하지 않으므로 두 카메라 추론과 함께 대기열/클립 완료 지연도 측정한다.

## 격리 검증

`python -m unittest backend.tests.test_face_verification -v`

가짜 모델 출력, 합성 이미지/영상, 실제 영상 인코더로 출력 재검사·복구 횟수 제한·
실패 시 비공개 결과 정리·원본 탐지 합집합·출력 계약·체크섬 오류를 검증한다.
실제 얼굴, 모델 가중치, GPU, 카메라, 외부 API는 사용하지 않는다.

`backend.tests.test_verified_source_policy`는 PostgreSQL 격리 DB에서 v19→20 이관,
검사 보고 누락/결과 불일치 차단, worker 정리 전 삭제 금지, 삭제 실패 복구,
원본 비공개와 기존 worker 호환성을 확인한다.

## 이 PC의 모델 준비 결과

공식 README의 공개 가중치 폴더에서 명시적 승인 후 다운로드한 Resnet50_Final.pth를
`torch.load(weights_only=True)`로 읽고, upstream `b984b4b775b2c4dced95c1eadd195a5c7d32a60b`
모델 구조로 내보냈다. 체크포인트 외부 코드/불안전 pickle 로딩은 허용하지 않았다.

- 원본 가중치 SHA256: `6d1de9c2944f2ccddca5f5e010ea5ae64a39845a86311af6fdf30841b0a5a16d`
- ONNX SHA256: `4619df1d0ec99b5fa5ccde5c590222e3819d9ae76dc23c506bde47a356d0f617`
- CUDAExecutionProvider, 640×480 인물 없는 입력 10회: 평균 약 40ms/프레임
- SCRFD+RetinaFace 전체 경로: 인물 없는 이미지 1장과 10프레임 영상의 최종 검사·해시 통과

이 수치는 해당 PC의 합성 입력 검사이며 실제 얼굴 정확도나 두 카메라 동시 처리량을
검증한 결과가 아니다. 가중치 파일 자체와 개인용 모델 준비 스크립트는 Git에 포함하지 않는다.
