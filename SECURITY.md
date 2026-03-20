# Security Policy / 보안 정책

## English

### Overview

Thank you for reporting security issues in Assistant Agent Kanban.

Because this project touches the local filesystem, workspace creation, patch apply flow, external AI runtimes (OpenCode/Codex), and a web UI, security issues should be handled with care.

### Please Do Not Report Sensitive Issues Publicly First

Please avoid opening a public GitHub issue first for topics such as:

- possible arbitrary code execution
- path traversal
- arbitrary file write/delete
- auth or permission bypass
- abuse of patch apply / verification flow
- exposure of sensitive config, tokens, or credentials
- target repository contamination risk

### How To Report

Please report security issues privately to the repository maintainer whenever possible. If no dedicated security email is documented, use the maintainer contact method shown on GitHub or another appropriate private communication channel.

Helpful report details include:

- a short summary of the issue
- the impact scope
- reproduction steps
- likely abuse scenario
- relevant environment or configuration details
- a minimal reproduction if possible

Please mask any sensitive values before sending them.

### Priority Security Areas

- privilege escalation through filesystem state or `metadata.json` manipulation
- workspace or target repo path contamination
- abuse of `git apply`, verification, or commit flow
- request upload / attachment handling issues
- arbitrary path access through FastAPI APIs
- sensitive information exposure through config or logs
- command injection or subprocess-related issues in worker execution

### Response Principles

When a valid security issue is confirmed, the intended response order is:

1. reproduce and understand the impact
2. minimize public exposure while a fix is prepared
3. prepare a fix or mitigation
4. update documentation if needed
5. publish the fix

The project is still MVP-oriented, so no strict SLA is promised yet. Still, reproducible and high-impact issues are treated with higher priority.

### Operational Safety Notes For Users

- keep the verification `repo_root` clean
- configure overlay behavior carefully for sensitive config files and credentials
- review authentication / authorization before exposing the app publicly
- avoid deploying directly into production without sufficient validation

---

## 한국어

### 안내

Assistant Agent Kanban의 보안 문제를 제보해 주셔서 감사합니다.

이 프로젝트는 로컬 파일시스템, workspace 생성, patch apply, 외부 AI 실행기(OpenCode/Codex), 웹 UI를 함께 다루기 때문에 보안 문제를 공개적으로 다루는 방식에 주의가 필요합니다.

### 공개 issue로 바로 올리지 말아 주세요

다음과 같은 내용은 공개 GitHub issue에 바로 올리지 않는 것을 권장합니다.

- 임의 코드 실행 가능성
- path traversal
- 임의 파일 쓰기/삭제
- 인증/권한 우회
- patch apply / verification 흐름 악용
- 민감한 설정, 토큰, credential 노출
- target repository 오염 가능성

### 제보 방법

가능하면 저장소 관리자에게 비공개로 알려 주세요. 별도 보안 메일이 문서화되어 있지 않다면 GitHub 프로필의 연락 수단이나 private communication channel을 사용해 주세요.

제보 시 포함하면 좋은 정보:

- 문제 요약
- 영향 범위
- 재현 방법
- 예상되는 악용 가능성
- 사용한 설정 또는 환경 정보
- 최소 재현 예시

민감한 정보는 반드시 마스킹해 주세요.

### 중요하게 보는 범주

- filesystem state / `metadata.json` 조작을 통한 권한 우회
- workspace 또는 target repo 경로 오염
- `git apply`, verification, commit 흐름 악용
- request upload / attachment 처리 문제
- FastAPI API를 통한 임의 경로 접근
- 설정 또는 로그를 통한 민감 정보 노출
- worker 실행 시 command injection 또는 subprocess 관련 취약점

### 대응 원칙

보안 문제가 확인되면 가능한 범위에서 아래 순서로 대응합니다.

1. 문제 재현 및 영향 범위 확인
2. 공개 범위 최소화
3. 수정 또는 완화책 준비
4. 필요 시 문서 업데이트
5. 수정 버전 공개

현재 프로젝트는 아직 MVP 성격이 강하므로, 엄격한 SLA를 약속하지는 않습니다. 다만 재현 가능하고 영향이 큰 문제는 우선순위를 높게 봅니다.

### 사용자를 위한 주의사항

- verification 대상 `repo_root`는 clean 상태로 유지
- 민감한 설정 파일과 credential은 overlay 정책을 신중히 구성
- 공개 서버로 노출하기 전 인증/권한 모델을 별도로 검토
- 충분히 검증되지 않은 production 환경에는 바로 적용하지 않기
