# release 스택의 LAN 시연 접속

관련 이슈: #152. 이 문서는 도메인·공인 접속이 준비되지 않은 경우의 B안입니다. 공인 IP·공유기·학교 네트워크 조건 확인과 HTTPS 방식 결정은 별도로 진행합니다.

## 접속 모드

| 선택 | 웹 바인딩 | 사용 범위 |
| --- | --- | --- |
| 기본 실행 | 127.0.0.1 | 서버 PC 내부 HTTP |
| `-Lan` | 0.0.0.0 | 신뢰하는 같은 LAN의 현장 PC·폰에서 HTTP |
| `-Https` | 127.0.0.1 + Caddy의 80/443 | 준비한 도메인의 HTTPS |

`-Lan`과 `-Https`는 동시에 사용할 수 없습니다. LAN 모드에서도 API 8000 포트는 localhost로 유지하고 PostgreSQL 포트는 공개하지 않습니다. 외부 기기는 웹 포트의 nginx를 통해 `/api`·보호된 `/storage`에 접근합니다. 기존 웹 화면과 nginx 설정은 변경하지 않습니다.

## 실행

첫 배포의 폴더·비밀키·DB 초기화·게시 이미지 준비는 [서버 배포 문서](20261013_Server_Release_Setup.md)를 따릅니다. `server.env`의 `PPE_COOKIE_SECURE=false`를 유지합니다. 배포 스크립트는 `-Lan` 선택에 따라 웹 바인딩을 결정하므로 주변 환경변수의 `PPE_WEB_BIND=0.0.0.0`만으로 기본 실행이 LAN 공개로 바뀌지는 않습니다.

아래 명령의 `$deploymentRoot`, `$releaseCommit`, `$apiImage`, `$webImage`는 위 배포 문서에서 준비한 값입니다.

```powershell
# 기존 초기화된 서버에서 같은 LAN 접속을 명시적으로 선택합니다.
./deploy/Release.ps1 -DeploymentRoot $deploymentRoot -Commit $releaseCommit -ApiImage $apiImage -WebImage $webImage -Lan

# 최초의 전용 서버 DB 생성이라면 위 명령에 -InitializeDatabase를 추가합니다.
```

LAN 옵션을 지원하는 release 커밋을 사용해야 합니다. 해당 기능이 없는 과거 release의 설정으로 `-Lan`을 지정하면 거부합니다. 기존 HTTPS 모드를 HTTP/LAN으로 바꾸는 작업은 기존 배포 규칙대로 별도 검토가 필요합니다.

Actions에서는 main의 `Release` 수동 실행에서 `deploy=true`, `lan=true`, `https=false`를 선택합니다. 기존 production 승인자·main 전용 브랜치 정책·서버 runner 조건을 그대로 적용합니다. `lan=true`가 배포 승인을 생략하거나 단독으로 포트를 열지는 않습니다.

## 기기별 주소

서버 PC의 LAN IPv4가 예를 들어 `192.168.0.20`, 웹 포트가 기본 `8080`이면 다음 주소를 사용합니다. 실제 IP는 서버 네트워크 설정에서 확인합니다.

- 서버 PC·네이티브 GPU worker: 기존 `http://127.0.0.1:8000` API 주소를 유지할 수 있습니다.
- 현장 송출·방송 프로그램: `http://192.168.0.20:8080`을 서버 주소로 지정합니다.
- 같은 Wi-Fi의 폰·다른 PC: 브라우저에서 `http://192.168.0.20:8080`에 접속합니다.

서버 PC·현장 PC·폰이 서로 통신할 수 있는 같은 네트워크여야 합니다. 게스트 Wi-Fi·AP isolation·학교 방화벽에서 기기 간 연결이 막혀 있으면 주소만 맞춰도 접속되지 않습니다. DHCP 예약으로 서버 IP를 유지하는 것을 권장합니다.

HTTP이므로 암호화되지 않습니다. 동의한 시연용 계정·자료와 신뢰하는 네트워크에서만 사용하고 인터넷 공유기의 포트포워딩 대상으로 지정하지 않습니다. Windows 방화벽에서 필요한 경우 **개인 네트워크·웹 포트·로컬 서브넷**으로 범위를 제한하여 별도로 허용합니다. 이 변경은 방화벽을 자동 수정하지 않습니다.

## 다시 localhost로 제한하기

```powershell
# -Lan 없이 실행하여 웹 포트를 localhost로 다시 제한합니다.
./deploy/Release.ps1 -DeploymentRoot $deploymentRoot -Commit $releaseCommit -ApiImage $apiImage -WebImage $webImage
```

성공한 LAN 여부는 `current-release.json`의 `lan`에 기록됩니다. 과거 기록에 해당 필드가 없으면 localhost 모드로 해석합니다. 실패 시 이미지뿐 아니라 이전 release의 웹 바인딩도 복구합니다. 같은 커밋에서 접속 모드만 바꾼 경우에도 이전 상태를 `previous-release.json`에 남깁니다. 스크립트 종료 시 임시 이미지·바인딩 환경변수를 복원합니다.

## 검증 범위

가짜 Docker 기반 테스트로 LAN 켜기/끄기, HTTPS 동시 선택 거부, 실패 시 양방향 바인딩 복구, 환경변수 복원을 확인합니다. Compose 설정에서는 기본 localhost·선택적 0.0.0.0 및 API localhost·DB 비공개를 확인합니다. Actions workflow 문법도 검사합니다.

실제 LAN 리스닝·Windows 방화벽 변경·폰 접속·외부 HTTPS 검증은 수행하지 않습니다. 해당 네트워크를 확정한 뒤 서버 실행과 실제 기기 접속을 별도로 승인받아 확인합니다. 따라서 이 변경만으로 #152 전체를 완료 처리하지 않습니다.
