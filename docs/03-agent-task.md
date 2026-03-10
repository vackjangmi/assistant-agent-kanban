# AI Agent Task Brief

이 문서는 AI coding agent에게 바로 전달할 수 있는 작업 지시서다.

## 해야 할 일

`docs/01-architecture-review.md` 와 `docs/02-implementation-plan.md` 를 기준으로,  
아래 시스템의 **MVP를 실제로 구현**하라.

### 시스템 개요
- Python 서버 하나를 실행한다
- 같은 프로세스에서 FastAPI와 worker loop를 함께 실행한다
- 파일/디렉토리 기반 칸반 상태를 관리한다
- 각 상태 변화에 맞춰 `opencode run` 을 호출한다
- Planner / Implementer / Reviewer / Committer 를 자동화한다
- 사람이 승인하는 단계는 유지한다
- 브라우저에서 SSE 기반 칸반 보드를 볼 수 있게 한다

---

## 구현 범위

### 1. Domain
구현할 것:
- 상태 enum
- metadata 모델
- board snapshot 모델
- transition validator
- task bootstrap / normalization
- atomic write 유틸
- lock manager

### 2. OpenCode adapter
구현할 것:
- `opencode run` subprocess 래퍼
- attach URL 지원
- JSON format log 저장
- final assistant text 추출
- timeout / failure 표준화

### 3. Workers
구현할 것:
- PlanningWorker
- ImplementerWorker
- ReviewerWorker
- CommitWorker

### 4. Workspace
구현할 것:
- clone-overlay workspace 생성
- overlay copy / symlink manifest
- branch 생성
- workspace cleanup 정책(최소한 명시)

### 5. Integration
구현할 것:
- workspace diff 생성
- integration repo clean 검사
- patch apply (`git apply --3way --index`)
- conflict 시 `todos` 복귀

### 6. Web
구현할 것:
- `GET /healthz`
- `GET /api/board`
- `GET /api/tasks/{task_id}`
- `GET /api/events`
- `GET /` (HTML dashboard)

---

## 명확한 제약

1. task 디렉토리 안에 전체 repo를 넣지 마라.
2. 상태 전이는 lock 하에서만 수행하라.
3. 사람이 직접 이동하는 승인 단계는 지워선 안 된다.
4. planner/reviewer는 read-only output 중심으로 설계하라.
5. oh-my-opencode 내부 state 파일을 state source로 사용하지 마라.
6. 구현 격리는 clone-overlay를 기본값으로 하라.
7. review 통과 후에만 integration repo에 patch를 반영하라.

---

## 상태별 동작

### requests
- `REQUEST.md` 를 가진 새 task를 감지
- metadata 초기화
- planner 큐 대상으로 삼음

### planning
- planner 실행 중
- 성공 시 `PLAN.md` 생성

### waiting-check-plans
- 사람이 plan 확인/수정
- 사람이 `todos` 로 옮기면 구현 시작 가능

### todos
- implementer 대기 상태

### implementing
- isolated workspace에서 구현 중

### waiting-reviews
- reviewer 대기 상태

### reviewing
- review 진행
- fail이면 `todos`
- pass이면 integration apply 후 `completed-reviews`

### completed-reviews
- integration repo에 코드 반영 완료
- 사람이 실제 실행 확인 가능

### integration-test-completed
- 사람이 테스트 완료 후 이동
- commit worker가 최종 commit 수행

### done
- 완료

---

## 산출물 요구사항

최소 산출물:

- 실행 가능한 Python 패키지
- 테스트 코드
- 예시 설정 파일
- README
- FastAPI 대시보드
- worker/service 구조
- `.opencode/agents/*.md` 예시 또는 동등한 prompt contract

---

## 권장 작업 순서

1. domain / models / enums
2. scanner / metadata / transitions / locks
3. planner worker
4. workspace manager
5. implementer worker
6. reviewer worker + integration manager
7. committer worker
8. FastAPI + SSE
9. recovery
10. 테스트 보강

---

## Definition of Done

아래를 모두 만족하면 완료다.

- 새 request 디렉토리를 만들면 planner가 자동으로 plan을 만든다
- 사람이 `todos` 로 옮기면 implementer가 workspace에서 작업한다
- implement 완료 시 review 대기 상태로 이동한다
- reviewer가 fail/pass를 분기한다
- pass 시 integration repo에 patch가 반영된다
- 사람이 테스트 후 `integration-test-completed` 로 옮기면 commit 후 done으로 이동한다
- board API와 SSE UI에서 전체 상태가 보인다
- 테스트가 있다
