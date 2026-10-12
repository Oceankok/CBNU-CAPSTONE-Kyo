# 장비 후보 인식: ChatGPT 구독 연동

장비 인식 제공업체를 `codex_chatgpt` 또는 `chatgpt_oauth`로 선택합니다. 기본값은 미설정입니다. 구독 한도를 소비하며, 일반 유료 API key로 자동 전환하지 않습니다. 공개 오픈소스·로컬 실행 시연을 위한 실험적 연결입니다. 상용·호스팅 서비스로 전환할 때는 별도 정책과 인증 방식을 검토합니다.

두 방식 모두 기존 `POST /api/equipment/recognitions`에서 SCRFD 비식별화를 완료한 JPEG만 전달합니다. AI 후보를 관리자가 수정·확정하는 API는 그대로 유지합니다. 프로젝트 관리자 로그인과 ChatGPT 제공업체 로그인은 서로 별개이며, 작업자 계정으로 외부 분석을 요청할 수 없습니다. `frontend/` 화면은 변경하지 않았습니다.

## 1. 로컬 Codex 구독 로그인

서버를 실행할 동일 Windows 사용자 계정으로 다음을 수행합니다. 로그인 명령은 브라우저를 엽니다. 이미 ChatGPT 로그인 상태이면 다시 로그인할 필요가 없습니다.

```powershell
Set-Location C:\Users\robin\Projects\CBNU-CAPSTONE-Kyo
codex login status
# 상태가 ChatGPT 로그인이 아닐 때만 실행합니다.
codex login
$env:PPE_EQUIPMENT_PROVIDER = 'codex_chatgpt'
# 모델은 필요 시 계정에서 사용 가능한 모델을 지정합니다.
# $env:PPE_EQUIPMENT_MODEL = 'gpt-6.1-sol'
```

이후 기존 서버 실행 절차를 사용합니다. Codex가 PATH에 없다면 실행 파일의 절대 경로를 `PPE_CODEX_EXECUTABLE`에 지정합니다. 프로세스에서는 API key 관련 환경변수를 제거하고 `forced_login_method="chatgpt"`를 지정합니다. 사용자 설정·규칙 로딩을 제외하고 셸 도구·웹 검색을 비활성화한 뒤 별도 임시 폴더에서 읽기 전용·임시 세션으로 실행합니다. 이미지, 출력 JSON과 스키마는 완료·실패·시간 초과 시 임시 폴더와 함께 제거합니다. Codex 실행 로그는 API 응답에 노출하지 않습니다.

이 방식은 CLI 시작 비용이 발생하고 로그인·한도 오류 일부가 `provider_unavailable`로 표시될 수 있습니다. 로컬 시연용으로 우선 비교합니다. OS에서 프로세스를 강제 종료한 경우 `.private_media/vlm_codex_*` 잔여 경로를 서버 종료 후 확인·정리합니다.

## 2. 프로젝트 전용 Sign in with ChatGPT

ID 토큰 서명 검증에 필요한 선택 의존성을 먼저 설치합니다. 2026-10-11 기준 이 개발 PC에는 `cryptography`를 추가 설치했습니다. 다른 PC에서는 아래 설치 명령을 수행합니다.

```powershell
Set-Location C:\Users\robin\Projects\CBNU-CAPSTONE-Kyo
python -m pip install -r backend/requirements-vlm.txt
python -m backend.equipment.chatgpt_auth login
python -m backend.equipment.chatgpt_auth status
python -m backend.equipment.chatgpt_auth models
```

로그인은 시스템 브라우저에서 진행하며 `127.0.0.1`의 임시 포트만 콜백을 받습니다. PKCE·state·nonce와 ID 토큰 서명·issuer·audience·만료를 검증하고 `chatgpt.tokens.use.direct` 동의가 있는 경우만 저장합니다. 첫 연결 시 등록한 클라이언트와 PC host ID는 다시 사용합니다. 2026-10-11 이 개발 PC에서 브라우저 로그인·구독 동의 및 계정별 모델 조회를 확인했습니다. 다른 계정·PC의 실제 지원 여부는 각각 확인해야 합니다.

`models`에 출력된 계정별 모델 slug 중 이미지 입력을 지원하는 모델을 선택한 후, **서버를 시작하는 같은 터미널**에 설정합니다.

```powershell
$env:PPE_EQUIPMENT_PROVIDER = 'chatgpt_oauth'
$env:PPE_EQUIPMENT_MODEL = 'gpt-6.1-sol' # models 목록에서 실제 사용 가능한 slug로 변경합니다.
```

공식 공개 `https://api.openai.com/v1/responses`에 `store=false`, `stream=true`로 요청합니다. `response.completed`를 확인한 경우만 JSON을 후보로 사용하고, 중간 응답·연결 종료·한도 초과를 성공으로 처리하지 않습니다. 완료 이벤트의 본문이 비어 있는 경우에는 스트리밍 중 수집한 본문을 완료 확인 후 검증합니다. 이 개발 계정에서는 `gpt-6.1-sol`의 이미지 요청을 확인했습니다. 다른 계정·모델의 이미지 지원은 별도로 확인합니다.

자격 증명은 `.private_media/chatgpt_auth/credentials.dat`에 보관합니다. Windows에서는 사용자 DPAPI로 암호화하고 Unix에서는 0600 파일 권한으로 저장합니다. DB·Git·웹 API에 토큰을 제공하지 않습니다. 만료가 가까우면 refresh token을 갱신하고 교체 토큰을 원자적으로 저장합니다. 여러 프로세스의 갱신은 lock 파일로 직렬화하며 사용 중이면 자동 반복 대신 실패를 반환합니다. 프로세스를 강제 종료해 `session.lock`이 남은 경우 관련 서버·로그인 프로그램을 모두 종료한 뒤 해당 파일만 제거합니다. 다른 PC에서는 새로 로그인합니다.

연결 해제 및 다른 계정 연결은 서버를 종료한 상태에서 수행합니다.

```powershell
python -m backend.equipment.chatgpt_auth logout
python -m backend.equipment.chatgpt_auth login
```

logout은 원격 세션 폐기를 시도하고 로컬 토큰을 제거합니다. 원격 폐기를 확인하지 못했다면 ChatGPT 설정에서도 앱 연결을 해제합니다. 삭제되지 않은 host ID는 같은 PC의 재등록에 계속 사용합니다.

## 3. 실패 및 수동 등록

`provider_auth_required`: 제공업체 재로그인이 필요합니다. `provider_usage_limit`: 구독 사용량 또는 공유 사용 가능 여부를 확인합니다. `provider_timeout`, `provider_rate_limited`, `provider_unavailable`: 이후 재요청하거나 기존 수동 장비 등록을 사용합니다. 잘못된 모델·미지원 이미지·JSON 형식 오류는 실패 처리하며 장비를 자동 등록하지 않습니다. 비식별화 실패 시 외부로 보내지 않습니다.

기존 장비 분석 호출 제한은 30초입니다. CLI 시작·토큰 갱신 때문에 이를 넘으면 시간 초과가 발생할 수 있으며, 실제 비교 후 제한 조정을 결정합니다. 실제 이미지 업로드 요청은 외부 전송과 구독 사용을 발생시킵니다. PPE 실시간 추론에는 이 연결을 사용하지 않습니다.

## 4. 검증

구독 제공업체 선택, API key 제외·구독 로그인 강제, 임시 파일 정리, 공식 스트리밍 요청, 완료 전 실패·한도 초과, 응답 검증, 토큰 갱신·동의 scope·nonce 검증 테스트를 추가했습니다. 실제 응답의 완료 본문 생략 사례를 보완하는 회귀 테스트를 추가한 뒤 구독 연동 테스트 9개와 이를 포함한 백엔드 전체 62개가 통과했습니다. 자동 회귀 테스트에서는 CLI·네트워크·로그인·GPU를 가짜 응답으로 대체했습니다.

승인 후 자동 검증 명령:

```powershell
Set-Location C:\Users\robin\Projects\CBNU-CAPSTONE-Kyo
python -m unittest backend.tests.test_subscription_provider backend.tests.test_equipment_recognition -v
python -m unittest discover -s backend -t . -q
```

실제 검증은 로그인 상태 확인 → 이미지 지원 모델 선택 → 서버 시작 → 관리자 인증 → 비식별화 가능한 시연 이미지 1장 업로드 → 후보/근거 확인 → 관리자 확정 → 장비 조회 순서로 수행합니다. 동일 이미지로 제공업체 설정만 바꿔 비교합니다. 정상 후보, 로그인 만료, 구독 한도 및 미지원 모델 실패를 따로 확인합니다.

### 2026-10-11 실제 연결 검증 결과

- 사람·기업 정보가 없는 합성 지게차 그림을 사용했습니다. 실제 산업 장비 사진의 인식 정확도나 얼굴 모자이크 품질을 평가한 테스트는 아닙니다.
- `codex_chatgpt`: 기존 ChatGPT 로그인으로 실제 이미지 요청을 수행하여 지게차 후보를 반환했습니다. 직접 제공업체 호출은 약 7.77초였습니다.
- `chatgpt_oauth`: 프로젝트 전용 로그인·구독 동의, 모델 목록 조회, `gpt-6.1-sol` 이미지 요청을 수행하여 지게차 후보를 반환했습니다. 수정 후 직접 호출은 약 5.55초였습니다. 서로 다른 그림 크기·실행 조건이므로 성능 비교 수치로 사용하지 않습니다.
- 두 방식 모두 격리 DB와 임시 근거 저장소에서 실제 SCRFD CUDA 비식별화 → 실제 구독 분석 → 후보 저장 → 근거 JPEG 제공 → 관리자 확정 → 장비 조회를 확인했습니다. HTTP API는 로컬 TestClient로 호출했으며 웹 화면이나 Swagger 조작은 수행하지 않았습니다.
- 근거 이미지의 미인증 요청은 401, 작업자 요청은 403이었습니다. 관리자 확정 전 장비 목록은 비어 있었고, 확정 후 한 건만 등록됐습니다. OAuth 방식에서 중복 확정 거절 409도 확인했습니다.
- 최초 OAuth 분석에서는 스트리밍 답변은 도착했지만 완료 이벤트의 출력 본문이 비어 있어 검증이 실패했습니다. 완료를 확인한 뒤 수집한 본문을 사용하도록 보완하여 실제 재호출과 자동 회귀 테스트를 통과했습니다.
- 실제 테스트에서는 개발 DB·장비 목록을 변경하지 않았으며 격리 DB·근거 파일은 테스트 종료 후 제거했습니다. 프로젝트 전용 ChatGPT 연결은 이후 실행을 위해 암호화 상태로 유지합니다. 실물 장비 인식 정확도·실제 구독 한도 도달·로그인 취소·장기 토큰 갱신은 별도로 확인합니다.

## 공식 근거

실물 지게차·드릴링 머신 사진 두 장에 대한 두 방식의 최종 백엔드 기능 검증은 [실물 사진 검증 결과](20261011_Equipment_Real_Photo_Validation.md)에 정리했습니다. 총 4개 조합에서 후보 분석·관리자 수정 등록·카메라 연결·PPE 미리보기가 통과했습니다.

- [Codex 인증](https://learn.chatgpt.com/docs/auth)
- [Codex 비대화형 실행](https://learn.chatgpt.com/docs/non-interactive-mode)
- [SIWC 오픈소스 구독 사용](https://developers.openai.com/siwc/token-sharing-open-source)
- [SIWC 로그인](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [SIWC 토큰 갱신](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [SIWC 모델·추론](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [프리뷰 제한](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
