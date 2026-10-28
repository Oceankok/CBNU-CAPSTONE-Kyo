# 장비 사진 인식·교육 초안의 호스트 처리

## 역할과 범위

API·PostgreSQL은 기존 Docker 서버를 유지합니다. 서버 PC의 기존 네이티브 `backend.processing.worker`가 장비 사진 비식별화와 Codex 실행을 담당합니다. 영상 송출 PC에서 인식하는 구조가 아닙니다. GPU 컨테이너와 별도 장치 관리 시스템은 추가하지 않습니다.

현재 시연 기본안은 두 기능 모두 `codex_chatgpt`입니다. 정식 API는 `openai`를 명시적으로 선택할 때만 사용하며, 구독 로그인·사용량 제한·시간 초과가 발생해도 유료 API로 자동 전환하지 않습니다. 기본 환경 파일은 기능을 비활성화한 상태로 제공합니다.

## 설정

Docker API의 `server.env` 또는 개발용 `.env`에 다음을 설정합니다.

```dotenv
PPE_PROCESSING_MODE=remote
PPE_EQUIPMENT_PROVIDER=codex_chatgpt
PPE_EDUCATION_PROVIDER=codex_chatgpt
```

기존 node 등록·processing worker 활성화·서버 주소·node token 설정은 그대로 사용합니다. 같은 네이티브 worker 프로세스에 다음 환경을 전달합니다. 예시 파일은 `deploy/host-ai.env.example`이며, Python이나 PowerShell이 이 파일을 자동으로 읽지는 않습니다.

```powershell
$env:PPE_HOST_AI_ENABLED = 'true'
$env:PPE_EQUIPMENT_PROVIDER = 'codex_chatgpt'
$env:PPE_EDUCATION_PROVIDER = 'codex_chatgpt'
# 선택 사항: 구독 계정에서 사용 가능한 모델을 명시합니다.
$env:PPE_EQUIPMENT_MODEL = ''
$env:PPE_EDUCATION_MODEL = ''
python -m backend.processing.worker
```

worker를 실행하는 Windows 계정에서 Codex CLI가 설치되어 있고 ChatGPT 구독으로 로그인되어 있어야 합니다. 다른 Windows 계정의 로그인은 공유되지 않습니다. 인증 파일·토큰을 Docker에 복사하거나 채팅에 게시하지 않습니다. 모델 미지정 교육 초안의 `model=codex-default`는 CLI 기본값 사용을 뜻하며 실제 모델 버전을 확정한 값은 아닙니다.

worker는 설정된 기능을 30초 유효한 capability로 알립니다. 활성 worker가 없거나 제공업체가 서로 다르면 요청을 503으로 거절합니다. 이는 설정 광고이며, 실제 로그인·모델 설치·API 키의 유효성을 검증했다는 뜻은 아닙니다. `PPE_HOST_AI_ENABLED`를 끄면 새 AI 작업을 받지 않으며, 기존 미디어 처리 기능은 유지합니다.

## 장비 사진 흐름

기존 `POST /api/equipment/recognitions?camera_id=...`의 JPEG/PNG 업로드와 201 응답, 후보·확정 API를 유지합니다. API는 파일 종류·크기·디코딩·카메라를 검사하고 비공개 작업으로 전달합니다. 관리자 요청은 최종 결과까지 최대 약 90초 기다립니다. 새로운 프론트 polling 상태는 추가하지 않습니다.

worker가 원본을 받아 SCRFD 비식별화를 완료한 JPEG만 선택한 공급업체에 전달합니다. 비식별화 실패 시 외부 이미지 요청을 수행하지 않습니다. 후보와 비식별 JPEG가 돌아오면 기존 `equipment_recognition`과 인증된 근거 이미지 조회를 사용합니다. 원본은 공개 미디어에 게시하지 않습니다.

관리자가 `POST /api/equipment/recognitions/{id}/confirm`으로 확인·수정하면 기존 `equipment` 테이블에 정상 장비로 등록됩니다. 이미 구현된 장비 조회·구역 연결·PPE 설정 API에서 이 장비를 사용합니다. 카메라와의 연결은 기존 카메라-장비 매핑 API로 명시적으로 설정하며, 사진만으로 연결·PPE 규칙을 자동 확정하지 않습니다. 근거 이미지는 기존 24시간 만료 정책을 따릅니다.

## 교육 흐름

기존 생성 API와 초안→관리자 수정·승인→게시 흐름을 유지합니다. 구독 모드에서는 주제·PPE 종류만 worker에 전달하며 사용자 ID·영상·통계·동의서를 Codex에 보내지 않습니다. 생성 중 추천 정보가 바뀌면 기존 저장 차단 검사를 적용합니다. 게시 취소 API는 자료를 초안으로 돌려 이후 작업자 조회에서 제외합니다.

## 정식 API로 전환

장비는 API 서버와 네이티브 worker의 `PPE_EQUIPMENT_PROVIDER=openai`, worker의 `PPE_EQUIPMENT_MODEL`·`PPE_EQUIPMENT_OPENAI_API_KEY`를 설정합니다. 사진 비식별화는 계속 네이티브 worker가 수행합니다. 추가 연결 코드 없이 명시적 설정으로 전환합니다.

교육은 API 서버의 `PPE_EDUCATION_PROVIDER=openai`와 `PPE_EDUCATION_MODEL`·`PPE_EDUCATION_OPENAI_API_KEY`를 설정하면 기존 정식 API 어댑터를 사용합니다. 이 경우 교육 텍스트 요청은 API 서버에서 수행합니다. 키는 해당 기능의 전용 변수만 읽으며 다른 기능의 키를 자동 재사용하지 않습니다.

실제 구독 호출은 구독 사용량을 소비할 수 있으며, 정식 API 호출은 별도 과금 대상입니다. 코드 반영만으로 실제 외부 호출이나 배포 설정을 변경하지 않습니다.

## 작업·파일 수명과 접근

스키마 v15에 `host_ai_job`·`host_ai_worker`를 추가합니다. 기존 이벤트 미디어 작업에 가짜 이벤트를 만들지 않습니다. 이 변경은 새 기능의 테이블 초기화이며, 이전 SQLite 데이터를 PostgreSQL로 가져오는 작업은 포함하지 않습니다.

원본은 `.private_media/host_ai/{job_id}.image`에 잠시 보관하며 worker node 인증·작업 lease가 모두 일치할 때만 받습니다. 결과는 스키마·크기 검사를 거칩니다. 완료·실패·요청 종료 시 원본 삭제를 시도하고, 요청 소비 후 큐의 입력·결과 본문을 비웁니다. 서버 중단·삭제 실패로 남은 파일은 기존 maintenance의 만료 정리로 재시도합니다.

최대 대기/처리 시간은 90초, 활성 AI 요청은 최대 4건입니다. 만료된 결과는 게시하지 않습니다. worker는 작업별 비공개 임시 폴더를 정리하고 서버에 cleanup을 확인합니다. 정리 미확인 작업이 있으면 해당 worker는 다음 미디어·AI 작업을 받지 않습니다. worker 재시작 시 기존 journal 복구 방식을 재사용합니다.

미디어 작업을 먼저 확인하고 없을 때 AI 작업을 받습니다. 추론 busy 신호가 있으면 AI 작업 claim과 사진 비식별화를 대기합니다. 이미 실행 중인 외부 Codex 요청을 강제 선점하는 구조는 아닙니다.

## 후속 검증

격리 테스트는 가짜 Codex·OpenAI 응답과 임시 DB/이미지만 사용합니다. 실제 계정 로그인, 사진의 외부 전송, 모델 GPU 실행, 유료 API 호출, 서버/worker 재시작은 별도 승인 후 검증합니다. 프론트 화면은 수정하지 않습니다. 번역·선호 언어·영상 자막은 프론트 담당과 별도 합의합니다.

구현 참고: [Codex 비대화형 실행](https://developers.openai.com/codex/noninteractive/), [OpenAI 이미지 입력](https://developers.openai.com/api/docs/guides/images-vision).
