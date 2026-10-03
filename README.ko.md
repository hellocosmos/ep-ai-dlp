# EP AI DLP — 엔드포인트 AI 데이터 보호

**Windows 애플리케이션에서 AI 서비스로 민감정보가 전송되기 전에 검사·통제하는 경계를 탐구합니다.**

[English](README.md) · [한국어](README.ko.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md)

> 실험 단계 연구 공개본입니다. 상용 DLP 대체품이나 완전한 정보유출 방지를 주장하지 않습니다. 영어 README가 기준 문서이며 관리 콘솔은 영어입니다.

실제 콘솔 화면에 합성 UI 데이터를 표시했습니다. 장비 이름·건수·정책 상태는 예시이며 고객 사용량이나 실시간 차단 증거가 아닙니다.

![Console overview](docs/assets/console-overview.png)

<table><tr><td width="50%"><img src="docs/assets/console-policy.png" alt="Policy"></td><td width="50%"><img src="docs/assets/console-devices.png" alt="Devices"></td></tr></table>

![Inspection events](docs/assets/console-events-dark.png)

## 구현 범위

- Rust 기반 선택적 TCP/TLS 검사, 제한된 요청 해석과 결정적 탐지.
- 설정된 Windows 실행 파일 경로의 새 프로세스를 자동 포착하는 서비스. 이 경로에서는 전용 브라우저 프로필이나 프록시 실행 옵션이 필요 없습니다.
- 11개 규칙: 자격 증명 패턴, 개인키 표식, 이메일·카드번호·한국 휴대전화·주민등록번호.
- 일회용 등록, 장비에 결합된 Ed25519 서명 정책, 규칙별 차단/관찰, 적용 버전 보고.
- Next.js·NestJS·PostgreSQL 관리 콘솔과 원문을 제외한 메타데이터 이벤트.

단일 조직·공통 정책입니다. 목적지와 실행 파일은 에이전트에서 설정합니다. 응답 검사·파일 추출·맥락 기반 분류·Agent IAM은 이 범위에 포함되지 않습니다.

```text
Windows application -> TCP capture -> Rust TLS / DLP -> AI destination
                           ^
                  signed policy / metadata
                           |
              Next.js -> NestJS -> PostgreSQL
```

## 로컬 콘솔 실행

Node.js 22.19+, npm, Docker Compose가 필요합니다. 콘솔 실행만으로 PC가 보호되지는 않습니다.

```bash
git clone https://github.com/hellocosmos/ep-ai-dlp.git
cd ep-ai-dlp
npm ci
npm run bootstrap
docker compose --env-file .local/managed.env -f deploy/compose.yaml up -d
npm run build
```

별도 터미널에서 `npm run dev:server`와 `npm run start:console`을 실행하고 `http://127.0.0.1:3100`에 접속합니다. 무작위 초기 자격 증명은 비공개 `.local/managed.env`에 생성됩니다.

## 검증과 한계

통제된 Windows HTTPS 목적지에서 정상 요청 도착과 합성 민감 요청 미도달을 확인했습니다. 일반 Chrome의 서비스 재시작 후 허용·차단, 실제 ChatGPT 정상 대화 1회를 확인했습니다. 이는 광범위한 실서비스 호환성 증거가 아닙니다.

**ChatGPT 부수 요청의 오탐 가능성이 남아 있습니다. 실제 파일 업로드, 여러 서비스·브라우저, 차단 안내 UX, 성능, 재부팅·강제 종료 복구와 변조 저항성은 추가 검증이 필요합니다. 서비스 장애 시 캡처가 풀릴 수 있으므로 중단 없는 fail-closed 보호를 주장하지 않습니다. macOS 캡처와 불변 감사 저장소는 미구현입니다.**

## 공개 목적

보안 제품 설계자 Jaemyung Kim이 아키텍처·위협 모델·검증 기준을 정하고 AI coding agent의 도움으로 구현한 기술 실험입니다. 구현뿐 아니라 확인된 결과와 실패 경계를 공유합니다.

원본 코드는 MIT이며 Windows redirector·WinDivert 등 제3자 구성요소는 각 라이선스를 유지합니다. 이전 Python 브라우저 및 .NET 실험은 별도 실행 경로로 보존했습니다. 운영 자격 증명·원본 실험 자료·내부 사업 문서는 공개본에서 제외했습니다.

[English reference](README.md) · [Setup](docs/getting-started.md) · [Windows lab](docs/windows-lab.md) · [Architecture](docs/architecture.md) · [Validation](docs/validation.md) · [Screenshots](docs/screenshot-provenance.md) · [Security](SECURITY.md) · [Licenses](THIRD_PARTY.md)
