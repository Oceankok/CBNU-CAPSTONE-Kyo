# 스타터의 명확한 시작 전 실패 재시도

기존 창에서 새 세션 child가 설정/로컬 준비 단계에 실패하고 session.json을 생성하지 못한 경우, 기존에는 다음 새 시작도 FileNotFoundError로 막혔다.

새 모드 child의 ContextClient 구성 및 SessionRunner.create 단계(서버 요청/촬영 전)에 한정해 실패 exit78을 사용한다. 이후 lock/서버 연결/촬영/송신 실패는 이 코드로 바꾸지 않는다. 스타터는 본인이 실행한 new child가78로 종료됐고 session.json이 없을 때만 다음 새 시작을 허용한다. 이는 stop ACK를 만들어 주는 것이 아니며 상태 문구도 ACK 확인이라 하지 않는다.

기존 journal, 손상 journal, resume 모드, 일반 exit0/1/강제종료에서 journal이 없는 경우는 계속 차단한다. 오류 후 폴더나 임시파일을 자동삭제하지 않는다. 새 시작은 별도 UUID 폴더를 사용한다. 세션 생성 뒤 손상/삭제와 import 자체 실패 등 명확한 exit78을 얻지 못한 상태는 자동복구하지 않는다. 인증값을 argv/오류상태/파일에 새로 노출하지 않는다.

전용 synthetic subprocess-mock 회귀를 포함하며 실제 Windows GUI/권한/카메라로 실행한 검증은 아니다. 운영 기본 설정과 capture-context opt-in 상태는 변경하지 않는다. PG검증 후보와 이후API패치에 이어지는 별도후속이며 DB이관/쿼리를 바꾸지 않는다.
