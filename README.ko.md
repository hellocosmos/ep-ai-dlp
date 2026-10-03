# EP AI DLP — 엔드포인트 AI 데이터 보호

**Windows 애플리케이션에서 AI 서비스로 민감정보가 전송되기 전에 검사·통제하는 경계를 탐구합니다.**

[English](README.md) · [한국어](README.ko.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md)

> 실험 단계 연구 공개본입니다. 상용 DLP 대체품이나 완전한 정보유출 방지를 주장하지 않습니다. 영어 README가 기준 문서이며 관리 콘솔은 영어입니다.

실제 콘솔 화면에 합성 UI 데이터를 표시했습니다. 장비 이름·건수·정책 상태는 예시이며 고객 사용량이나 실시간 차단 증거가 아닙니다.

![Console overview](docs/assets/console-overview.png)

<table><tr><td width="50%"><img src="docs/assets/console-policy.png" alt="Policy"></td><td width="50%"><img src="docs/assets/console-devices.png" alt="Devices"></td></tr></table>

![Inspection events](docs/assets/console-events-dark.png)

## Jev에서 영감을 받은 사내 온프레미스 판단 API

> “Jev에서 영감을 받아 AI DLP에 작은 판단 모델을 적용해봤습니다. 정규식은 식별자나 알려진 비밀정보 패턴을 찾는 데 유용하지만, 거래조건과 업무 맥락, 정책의 의미까지 판단하기에는 한계가 있었습니다. 그렇다고 민감정보를 검사하려고 상용 AI API에 보내면 또 다른 외부 전송 문제가 생깁니다. 그래서 사내 API 서비스로 제공할 판단 계층과 업무 데이터 파인튜닝을 직접 구현하고 실험했습니다. 해보니 꽤 쓸 만했습니다. 더 발전시킬 가치가 있는 아이디어라고 느꼈습니다.” — 김재명

정규식·하드 보안 규칙에 의미 판단을 더했습니다. 모델은 점수를 반환하고 별도 정책 엔진이 허용·차단·검토·식별자 제거, 권한과 승인을 집행합니다. **배포 목표는 회사 내부 온프레미스 LLM API 서버입니다.** 직원 PC의 에이전트는 정책을 집행하고 사내 판단 서비스를 이용하며, 직원마다 LLM을 실행하지 않습니다. 검사 텍스트는 PC에서 사내 서버로 전송되므로 보호 경계는 개별 PC가 아니라 회사 내부 환경입니다. 현재 UI의 “Local Judge”는 실험 화면 이름입니다. Jev 공식 API 연동이나 제휴를 뜻하지 않습니다.

동일한 M4 Max·BF16·합성 시험 736건에서 원본→파인튜닝 정확도는 **Decider 2B 66.6→81.8%, Jeff Qwen 2B 82.7→89.9%, Jeff Gemma4 E2B 83.8→91.2%**였습니다. 학습본 API 중앙값은 각각 **68/87/121ms**입니다. 재사용한 54개 합성 원문 계열의 실험이며 운영 정확도 보증은 아닙니다. 새로운 소형 모델을 Windows 차단 경로나 실행 중인 기본 모델에 적용하지 않았습니다.

M4 Max는 개발·벤치마크 장비이며 직원 PC 요구 사양이나 운영 서버 용량 산정 결과가 아닙니다. 실제 사내 GPU 서버 운영과 엔드포인트 연동은 별도 검증이 필요합니다.

[실행 가이드](docs/local-judge.md) · [독립 판단 API](docs/jev-api.md) · [학습 파이프라인](judge/training/README.md) · [상세 실측 결과](research/judge-candidates/SMALL_MODEL_FINETUNING.md)


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
