# Security Policy

Assistant Agent Kanban의 보안 문제를 제보해 주셔서 감사합니다.

이 프로젝트는 로컬 파일시스템, workspace 생성, patch apply, 외부 AI 실행기(OpenCode/Codex), 웹 UI를 함께 다루기 때문에 보안 이슈를 공개적으로 다루는 방식에 주의가 필요합니다.

## 보안 이슈를 공개 issue로 올리지 말아 주세요

다음과 같은 내용은 공개 GitHub issue에 바로 올리지 않는 것을 권장합니다.

- 임의 코드 실행 가능성
- 파일 경로 탈출(path traversal)
- 임의 파일 쓰기/삭제
- 인증/권한 우회
- patch apply / verification 흐름을 악용할 수 있는 문제
- 민감한 설정, 토큰, credential 노출
- target repository 오염 가능성

이런 이슈는 공개적으로 먼저 논의되면 사용자에게 위험을 줄 수 있습니다.

## 어떻게 제보하면 되나요?

가능하면 저장소 관리자에게 비공개로 알려 주세요. 현재 별도 보안 메일 주소가 문서화되어 있지 않다면, GitHub 프로필의 연락 수단 또는 private communication channel을 사용해 주세요.

제보 시 포함해 주시면 좋은 정보:

- 문제 요약
- 영향 범위
- 재현 방법
- 예상되는 악용 가능성
- 사용한 설정 또는 환경 정보
- 가능하다면 최소 재현 예시

민감한 정보가 포함된다면, 실제 credential이나 개인 정보는 마스킹해 주세요.

## 어떤 종류의 제보가 중요한가요?

특히 아래 범주를 중요하게 봅니다.

- filesystem state / `metadata.json` 조작을 통한 권한 우회
- workspace 또는 target repo 경로 오염
- `git apply`, verification, commit 흐름 악용
- request upload / attachment 처리 문제
- FastAPI API를 통한 임의 경로 접근
- 설정 또는 로그를 통한 민감 정보 노출
- worker 실행 시 command injection 또는 subprocess 관련 취약점

## 대응 원칙

보안 이슈가 확인되면 가능한 범위에서 아래 순서로 대응합니다.

1. 문제 재현 및 영향 범위 확인
2. 공개 범위 최소화
3. 수정 또는 완화책 준비
4. 필요 시 문서 업데이트
5. 수정 버전 공개

아직 이 프로젝트는 MVP 성격이 강하므로, 응답 SLA나 지원 버전 정책을 엄격하게 약속하지는 않습니다. 다만 재현 가능하고 영향이 큰 문제는 우선순위를 높게 보고 처리하려고 합니다.

## 사용자를 위한 주의사항

현재 프로젝트를 사용할 때는 다음을 권장합니다.

- verification 대상 `repo_root`는 clean 상태로 유지
- 민감한 설정 파일과 credential은 overlay 정책을 신중히 구성
- 공개 서버로 노출하기 전 인증/권한 모델을 별도로 검토
- 테스트되지 않은 production 환경에 바로 적용하지 않기

보안 문제를 책임감 있게 제보해 주셔서 감사합니다.
