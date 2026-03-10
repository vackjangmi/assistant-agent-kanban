# 구현 계획서 (AI Agent 실행용)

## 목표

다음 시스템을 Python으로 구현한다.

- 파일/디렉토리 기반 칸반 상태 머신
- `opencode run` 기반 Planner / Implementer / Reviewer / Committer orchestration
- metadata / lock / recovery
- isolated workspace (`clone-overlay`)
- FastAPI + SSE 기반 read-only dashboard

이 문서는 **실제 구현 순서**와 **acceptance criteria**를 정의한다.

---

## 기술 스택

- Python 3.11+
- FastAPI
- Uvicorn
- Pydantic v2
- watchfiles
- filelock
- Jinja2 또는 순수 HTML 템플릿
- subprocess (`opencode run`, `git`)
- pytest

선택:
- orjson
- structlog
- tenacity
- rich (CLI 보조 도구가 필요할 때만)

---

## 패키지 구조 제안

```text
src/fs_kanban_agent/
├─ __init__.py
├─ config.py
├─ models.py
├─ enums.py
├─ scanner.py
├─ metadata_store.py
├─ locks.py
├─ transitions.py
├─ events.py
├─ opencode_adapter.py
├─ workspace_manager.py
├─ integration_manager.py
├─ recovery.py
├─ services/
│  ├─ board_service.py
│  └─ task_service.py
├─ workers/
│  ├─ base.py
│  ├─ planner.py
│  ├─ implementer.py
│  ├─ reviewer.py
│  └─ committer.py
└─ api/
   ├─ app.py
   ├─ routes.py
   ├─ sse.py
   └─ ui.py
```

테스트:

```text
tests/
├─ test_scanner.py
├─ test_transitions.py
├─ test_metadata_store.py
├─ test_locks.py
├─ test_planner_worker.py
├─ test_implementer_worker.py
├─ test_reviewer_worker.py
├─ test_committer_worker.py
├─ test_recovery.py
└─ test_api.py
```

---

## Phase 0 — 설정/부트스트랩

### 할 일
1. 프로젝트 초기화
2. 설정 모델 작성
3. 상태 디렉토리/런타임 디렉토리 bootstrap 함수 작성
4. Enum / Pydantic 모델 정의

### 주요 모델
- `TaskState`
- `TaskMetadata`
- `BoardSnapshot`
- `WorkerLease`
- `WorkspaceSpec`
- `IntegrationSpec`
- `RunResult`

### acceptance criteria
- 설정 파일 없이도 sensible default로 기동 가능
- 첫 실행 시 누락된 state/runtime 디렉토리를 자동 생성
- 모든 상태값이 enum으로 통제됨

---

## Phase 1 — 파일시스템 스캐너 + metadata store

### 할 일
1. 상태 디렉토리 전체 스캔
2. task 디렉토리 인식
3. `metadata.json` 읽기/생성/업데이트
4. canonical task_id / slug 생성
5. atomic write 유틸 구현

### 상세 요구사항
- 사람이 `requests/<제목>/REQUEST.md`를 만들면 스캐너가 task로 인식
- `metadata.json` 없으면 bootstrap
- title과 dir name 불일치 허용
- board snapshot은 매번 스캔으로 재구성

### acceptance criteria
- 빈 상태 디렉토리 스캔 가능
- `REQUEST.md`만 있는 폴더도 정상 bootstrap
- `metadata.json`은 temp file + replace로 저장
- 스캔 결과가 deterministic

---

## Phase 2 — lock / transition engine

### 할 일
1. per-task lock 구현
2. transition validator 구현
3. state move 유틸 구현
4. `history` 기록
5. `lease` / heartbeat 갱신

### transition rule
허용 전이만 엔진에서 통과시킨다.

#### 허용 전이
- requests -> planning
- planning -> waiting-check-plans
- waiting-check-plans -> todos
- todos -> implementing
- implementing -> todos
- implementing -> waiting-reviews
- waiting-reviews -> reviewing
- reviewing -> todos
- reviewing -> completed-reviews
- completed-reviews -> human-verifying
- human-verifying -> todos
- human-verifying -> done

### acceptance criteria
- 동시에 두 worker가 같은 task를 잡지 못함
- transition 시 metadata.state와 실제 디렉토리 상태가 일치
- 잘못된 전이는 에러로 처리
- lock path는 task 바깥 stable runtime dir에 있음

---

## Phase 3 — OpenCode adapter

### 할 일
1. `opencode run` subprocess 래퍼 작성
2. cwd 지정
3. attach URL 지원
4. timeout / cancellation 처리
5. stdout/stderr/raw JSON log 저장
6. final assistant text 추출

### 입력 예시
- planner prompt
- implementer prompt
- reviewer prompt
- commit prompt

### 요구사항
- `--attach` 설정 시 attach 사용
- 미설정 시 일반 `opencode run`
- `--format json` 기본 사용
- 각 phase run log를 `_runtime/runs/{task_id}/...`에 저장

### acceptance criteria
- mock subprocess로도 테스트 가능
- 실패/timeout/cancel이 `RunResult`로 표준화
- planner/reviewer 출력 markdown을 안전하게 추출 가능

---

## Phase 4 — PlanningWorker

### 입력 상태
- `requests`

### 흐름
1. task lock 획득
2. task bootstrap / canonical rename
3. `planning` 이동
4. planner prompt 생성
5. `opencode run`
6. final text를 `PLAN.md` 저장
7. metadata.plan.revision 갱신
8. `waiting-check-plans` 이동

### planner prompt 계약
반드시 아래 섹션을 포함하게 한다.

- Summary
- Scope
- Out of Scope
- File Map
- Step-by-step Plan
- Validation Plan
- Acceptance Criteria
- Risks
- Open Questions

### acceptance criteria
- `REQUEST.md`가 있으면 planner가 자동 실행됨
- `PLAN.md`가 생성됨
- 실패 시 task가 `planning`에 고착되지 않고 error 기록 후 `requests` 또는 재시도 정책으로 처리됨

---

## Phase 5 — WorkspaceManager + ImplementerWorker

### 목표
review 전까지는 **항상 격리된 workspace** 에서 구현한다.

### WorkspaceManager 할 일
1. workspace root 생성
2. local clone 수행
3. base branch checkout
4. task branch 생성
5. overlay copy / symlink 적용
6. workspace spec metadata 반영

### clone 명령 권장
```bash
git clone --reference-if-able <repo_root> --dissociate <repo_root> <workspace_repo>
```

### ImplementerWorker 흐름
1. `todos` 감지
2. task lock 획득
3. workspace 준비
4. `implementing` 이동
5. implementer prompt 실행 (cwd=workspace repo)
6. focused validation 명령 실행
7. 결과 요약을 `WORK-{n}.md` 저장
8. 실제 workspace git 변경이 있으면 `waiting-reviews` 이동
9. 변경이 없거나 구현 실패면 `todos` 복귀 + error 기록

### implementer prompt 계약
최종 응답에는 아래가 있어야 한다.

- Summary
- Files Changed
- Commands Run
- Validation Result
- Known Risks
- Reviewer Notes

### acceptance criteria
- workspace path가 task 디렉토리 밖에 생성됨
- implementer run은 workspace cwd에서 수행됨
- `WORK-001.md`가 생성됨
- 재작업 시 `WORK-002.md`, `WORK-003.md` 증가

---

## Phase 6 — ReviewerWorker + IntegrationManager

### ReviewerWorker 흐름
1. `waiting-reviews` 감지
2. lock 획득
3. `reviewing` 이동
4. reviewer prompt 실행
5. `REVIEW-{n}.md` 저장
6. verdict 분기

#### verdict = NEEDS_CHANGES
- error/finding 요약 기록
- `todos` 이동

#### verdict = PASS
- `completed-reviews` 이동

### reviewer prompt 계약
최종 응답에는 아래가 있어야 한다.

- Verdict (`PASS` 또는 `NEEDS_CHANGES`)
- Acceptance Criteria Check
- Findings
- Risks
- Integration Readiness
- Required Follow-ups

### Human verification 시작 시 할 일
1. workspace diff 생성
2. target repo clean 확인
3. patch 생성
4. patch apply (`git apply --3way --index`)
5. metadata.integration 갱신

### acceptance criteria
- review pass/fail이 분기됨
- `completed-reviews` 시점에는 아직 target repo를 건드리지 않음
- `human-verifying` 시점에는 target repo에서 사람이 실행 가능

---

## Phase 7 — CommitWorker

### 입력 상태
- `human-verifying`

### 흐름
1. target repo 상태 검증
2. commit prompt 또는 규칙 기반 commit message 생성
3. `COMMIT.md` 저장
4. `git commit`
5. sha 기록
6. `done` 이동

### acceptance criteria
- 사람이 `human-verifying -> done` 승인 시 commit 수행
- commit sha가 metadata와 `COMMIT.md`에 기록됨
- commit 실패 시 done으로 이동하지 않음

---

## Phase 8 — Recovery

### 할 일
1. startup orphan scan
2. stale heartbeat 판단
3. recovering policy 적용
4. recovery event SSE 발행

### 기본 정책
- planning orphan -> requests
- implementing orphan -> todos
- reviewing orphan -> waiting-reviews

### acceptance criteria
- 서버가 죽었다 다시 떠도 stuck task 정리 가능
- recovery 결과가 board/UI에 보임

---

## Phase 9 — FastAPI + SSE Dashboard

### API
- `GET /healthz`
- `GET /api/board`
- `GET /api/tasks/{task_id}`
- `GET /api/events`
- `GET /`

### UI 요구사항
- 10개 상태 컬럼
- 각 카드에 최소 표시:
  - title
  - task_id
  - updated_at
  - current iteration
  - error badge
- 자동 새로고침은 SSE 기반
- 수동 새로고침 버튼도 제공

### SSE 이벤트
- `board_snapshot`
- `task_updated`
- `task_moved`
- `worker_heartbeat`
- `recovery_event`

### acceptance criteria
- 브라우저를 열면 현재 board snapshot이 보임
- task 이동 시 자동으로 카드 위치가 갱신됨
- 새로고침 없이 상태 변화를 볼 수 있음

---

## 설정 파일 예시

```yaml
kanban_root: ./ai-kanban
repo_root: .
base_branch: main

opencode:
  binary: opencode
  attach_url: http://127.0.0.1:4096
  planner_agent: fs-kanban-planner
  planner_model: null
  implementer_agent: fs-kanban-implementer
  implementer_model: null
  reviewer_agent: fs-kanban-reviewer
  reviewer_model: null
  commit_agent: plan
  commit_model: null
  timeout_seconds: 1800

workspace:
  strategy: clone-overlay
  root: ./ai-kanban/_runtime/workspaces
  overlay_copy:
    - .env
    - .env.local
    - .fvm
    - android/local.properties
    - .tool-versions
    - .npmrc
  overlay_symlink: []

locks:
  heartbeat_seconds: 10
  stale_after_seconds: 60
```

---

## 구현 우선순위

### 1순위
- Phase 0 ~ 4
- Planning 자동화
- Board snapshot API

### 2순위
- Implementer / Reviewer
- clone-overlay
- SSE UI

### 3순위
- CommitWorker
- Recovery
- polish

---

## 테스트 전략

### 단위 테스트
- state validation
- metadata atomic write
- lock acquisition / release
- scanner normalization
- prompt renderer

### 통합 테스트
- request 생성 -> plan 생성
- todos 이동 -> implementation -> review
- review fail -> todos 복귀
- review pass -> completed-reviews
- completed-reviews -> human-verifying -> done
- server restart recovery

### e2e smoke
- tmp repo 생성
- sample request 작성
- fake opencode adapter로 전체 흐름 돌리기
- board API / SSE 검증

---

## Definition of Done

아래가 모두 충족되면 완료다.

1. 상태 디렉토리만으로 workflow가 추적된다
2. metadata/lock/recovery가 동작한다
3. planner/implementer/reviewer가 자동 실행된다
4. 구현은 isolated workspace에서 수행된다
5. human verification 시작 후 target repo에서 사람이 실제로 실행 가능하다
6. FastAPI board에서 상태가 실시간 반영된다
7. 테스트가 작성되어 있다

---

## 구현 중 금지사항

- task 디렉토리 안에 전체 repo workspace를 두지 말 것
- `oh-my-opencode` 내부 상태파일을 source of truth로 쓰지 말 것
- integration 전에 원본 repo에 변경을 미리 반영하지 말 것
- 수동 승인 단계를 삭제하지 말 것
- lock 없는 rename/state change를 하지 말 것
