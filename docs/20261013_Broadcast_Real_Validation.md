# 이벤트 방송 실제 음성 검증

관련 이슈: #135 / PR #148

2026년 10월 13일 Windows PC에서 별도 HTTP API 서버 프로세스와 현장 프로그램 프로세스를 실행하여 검증했습니다. 임시 SQLite DB·계정·node·카메라 연결을 사용했으며 기존 개발 DB는 변경하지 않았습니다. GPU 추론, 외부 API, 언어팩 설치는 수행하지 않았습니다.

## 검증 절차

1. 현장 프로그램으로 설치 음성을 재검색하고 서버에 목록을 보고했습니다.
2. 관리자 API로 한국어 `ko-KR` / Heami와 영어 `en-US` / Zira를 각각 선택했습니다.
3. 방송 템플릿은 기본 언어 코드 `ko`, `en`으로 저장했습니다.
4. 기존 후보 이벤트 생성 서비스로 안전모 미착용 이벤트를 생성했습니다. 영상 없이 이벤트 메타데이터만 생성했습니다.
5. 서버가 이벤트 방송 명령을 생성하고 현장 프로그램이 HTTP로 수신하여 실제 Windows TTS를 실행했습니다.
6. 현장 프로그램이 완료 결과를 서버에 보고하고 관리자 API에서 결과를 확인했습니다.

## 결과

| PC 언어 / 음성 | 템플릿 언어 | 실제 출력 문구 | 결과 |
| --- | --- | --- | --- |
| ko-KR / Microsoft Heami Desktop | ko | 안전모를 착용하세요. 한국어 이벤트 방송 테스트입니다. | PASS |
| en-US / Microsoft Zira Desktop | en | Please wear your safety helmet. This is the English event broadcast test. | PASS |

두 명령 모두 `status=completed`, `spoken=true`, `reason=tts_completed`로 보고됐으며 실제 스피커에서 한국어·영어 방송이 들리는 것을 확인했습니다. 임시 서버를 종료하고 임시 DB와 현장 상태 파일을 정리했습니다.

동일 PC의 별도 프로세스로 API·현장 프로그램 경계를 확인했습니다. 실제 영상 추론 자동 연동과 서로 다른 PC 사이의 네트워크 연결은 별도 시스템 통합 검증 대상입니다.
