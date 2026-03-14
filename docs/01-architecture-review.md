# AI Coding Agent + 파일/디렉토리 기반 칸반 설계 검토

## 결론

이 구조는 **충분히 구현 가능**하다.  
다만 아래 4가지는 처음부터 설계에 반영하는 것이 좋다.

1. **상태 머신의 진실 소스는 우리 시스템의 `metadata.json` + 디렉토리 상태**로 두고,  
   `oh-my-opencode` 내부 상태(`.sisyphus/boulder.json` 류)에 의존하지 않는다.
2. **Task 디렉토리와 실제 코드 작업공간(workspace)을 분리**한다.  
   상태 디렉토리 안에 코드 전체를 넣고 옮기면 너무 무겁고, 이동도 비싸다.
3. **순수 git worktree 단독 전략은 기본값으로 채택하지 않는다.**  
   대신 **local clone + overlay(ignored/untracked 보강)** 전략을 기본으로 한다.
4. **FastAPI 서버와 worker들을 한 프로세스/한 앱에서 같이 띄우되, 내부는 명확히 분리**한다.  
   `watchfiles`는 “이벤트 진실 소스”가 아니라 **재스캔을 깨우는 신호**로만 사용한다.

---

## 왜 이 방향이 맞는가

### 1) `opencode run`은 자동화 전제에 잘 맞는다

OpenCode는 CLI에서 `opencode run [message..]`로 비대화형 실행을 공식 지원하고,  
실행 중인 `opencode serve`에 `--attach` 해서 매번 MCP cold boot를 피할 수 있다.  
또한 `--format json`으로 raw JSON 이벤트를 받을 수 있다.  
즉, **Python 오케스트레이터가 각 단계마다 프롬프트를 넘겨 1회성 실행하는 구조**가 매우 자연스럽다.

### 2) 커스텀 에이전트/커맨드/AGENTS.md를 프로젝트에 넣는 패턴이 공식 지원된다

OpenCode는 프로젝트 로컬의:

- `.opencode/agents/*.md`
- `.opencode/commands/*.md`
- 프로젝트 루트 `AGENTS.md`

를 공식적으로 지원한다.  
따라서 “Planner / Implementer / Reviewer 역할별 prompt contract”를 **리포지토리에 commit 가능한 Markdown**으로 관리할 수 있다.

### 3) oh-my-opencode는 런타임 가속기로 쓰고, 워크플로 상태는 외부에서 잡는 편이 안전하다

`oh-my-opencode`는 매우 강력하지만, 최근 공개 이슈와 릴리스 노트를 보면  
내부 orchestration state(예: `boulder.json`, worktree awareness, continuation 로직)는 계속 진화 중이다.

따라서 이 프로젝트에서는:

- **OpenCode/oh-my-opencode = 실행 엔진**
- **우리 Python 서버 = workflow/state machine/orchestration 엔진**

으로 분리하는 편이 안전하다.

이렇게 하면 향후 oh-my-opencode 내부 구현이 바뀌어도  
우리 칸반/락/워크스페이스/승인 게이트 구조는 흔들리지 않는다.

---

## 추천 전체 구조

```text
repo-root/
├─ AGENTS.md
├─ .opencode/
│  └─ agents/
│     ├─ fs-kanban-planner.md
│     ├─ fs-kanban-implementer.md
│     └─ fs-kanban-reviewer.md
├─ .kanban-agent/
│  ├─ requests/
│  ├─ planning/
│  ├─ waiting-check-plans/
│  ├─ todos/
│  ├─ implementing/
│  ├─ waiting-reviews/
│  ├─ reviewing/
│  ├─ completed-reviews/
│  ├─ human-verifying/
│  ├─ done/
│  └─ _runtime/
│     ├─ locks/
│     ├─ workspaces/
│     ├─ runs/
│     ├─ board-cache/
│     └─ events/
└─ src/fs_kanban_agent/
```

핵심 원칙:

- **Task 디렉토리**: REQUEST/PLAN/WORK/REVIEW/metadata 같은 **가벼운 문서와 상태**
- **Workspace 디렉토리**: 실제 코드 작업용 복제/클론 공간
- **Integration repo**: 사람이 실제로 실행해보는 기준 리포지토리(보통 원본 작업 트리)

---

## 상태 머신

### 상태 목록

- `requests`
- `planning`
- `waiting-check-plans`
- `todos`
- `implementing`
- `waiting-reviews`
- `reviewing`
- `completed-reviews`
- `human-verifying`
- `done`

### 전이 규칙

```mermaid
stateDiagram-v2
    [*] --> requests
    requests --> planning: Planner Worker (auto)
    planning --> waiting-check-plans: PLAN.md 생성 완료 (auto)
    waiting-check-plans --> todos: 사람이 PLAN 검토 후 이동 (manual)

    todos --> implementing: Implementer Worker (auto)
    implementing --> waiting-reviews: 구현 완료 + 실제 workspace 변경 확인 (auto)
    implementing --> todos: 구현 실패 또는 workspace 변경 없음 (auto)

    waiting-reviews --> reviewing: Reviewer Worker (auto)
    reviewing --> todos: 리뷰 실패 / 수정 필요 (auto)
    reviewing --> completed-reviews: 리뷰 통과 (auto)

    completed-reviews --> human-verifying: 사람이 verification 시작 + patch apply (manual)
    human-verifying --> todos: 사람이 reject + rollback (manual)
    human-verifying --> done: 사람이 approve + commit (manual)
```

### 전이 정책

#### 자동 전이
- `requests -> planning`
- `planning -> waiting-check-plans`
- `todos -> implementing`
- `implementing -> todos`
- `implementing -> waiting-reviews`
- `waiting-reviews -> reviewing`
- `reviewing -> todos`
- `reviewing -> completed-reviews`
- `human-verifying -> todos`
- `human-verifying -> done`

#### 수동 전이
- `waiting-check-plans -> todos`
- `completed-reviews -> human-verifying`

### 중요한 보강 규칙

1. **허용되지 않은 수동 전이**는 UI/로그에 경고하고 worker가 집지 않게 한다.
2. `completed-reviews`는 **AI review 통과 후 사람이 verification 시작을 대기하는 상태**다.
3. `human-verifying`에 들어갈 때만 target repo에 patch를 적용한다.
4. `done`으로 가는 순간에만 최종 commit을 만든다.

---

## Task 디렉토리 네이밍

사용자 아이디어처럼 `{제목}` 디렉토리만 써도 되지만, 실제 운영에서는 충돌/한글/공백/중복명 이슈가 생긴다.

### 권장 포맷

```text
TASK-20260310-0001__로그인-리팩토링
```

또는 slug까지 ASCII로 정규화:

```text
TASK-20260310-0001__login-refactor
```

### 권장 동작

- 사람이 `requests/로그인 리팩토링/REQUEST.md` 를 만들어도 됨
- Planner Worker가 처음 감지할 때:
  - `task_id` 발급
  - `metadata.json` 생성
  - canonical 디렉토리명으로 rename
- 화면에는 `metadata.title` 을 보여주면 된다

즉, **표시용 title**과 **파일시스템용 canonical name**은 분리하는 것이 좋다.

---

## Task 디렉토리 내부 계약

각 task 디렉토리는 가볍게 유지한다.

```text
TASK-20260310-0001__login-refactor/
├─ REQUEST.md
├─ PLAN.md                  # planning 이후
├─ WORK-001.md              # 구현 1차 결과
├─ REVIEW-001.md            # 리뷰 1차 결과
├─ WORK-002.md              # 재작업 시 증가
├─ REVIEW-002.md
├─ COMMIT.md                # 최종 commit 메시지/sha 기록
├─ metadata.json
└─ logs/
   ├─ planner-001.jsonl
   ├─ implementer-001.jsonl
   └─ reviewer-001.jsonl
```

### 파일 의미

#### `REQUEST.md`
사람이 작성하는 최초 요청서.

권장 섹션:
- 배경
- 목표
- 범위
- 비범위
- 제약
- 참고 파일/경로
- 완료 조건

#### `PLAN.md`
Planner가 생성하고 사람이 검토하는 실행 계획.

권장 섹션:
- 문제 요약
- 구현 범위
- 영향 파일 맵
- 단계별 작업 계획
- 테스트 계획
- acceptance criteria
- 리스크
- open questions

#### `WORK-{n}.md`
Implementer 결과 요약.

권장 섹션:
- 이번 iteration 목적
- 실제 변경 파일
- 실행한 명령
- 테스트 결과
- 남은 리스크
- reviewer가 볼 포인트

#### `REVIEW-{n}.md`
Reviewer 결과.

권장 섹션:
- verdict: `PASS` | `NEEDS_CHANGES`
- acceptance criteria 충족 여부
- 버그/리스크
- 수정 요청
- integration 적용 가능 여부

#### `COMMIT.md`
최종 commit 메시지와 sha 기록.

---

## `metadata.json` 권장 스키마

최소 스키마 예시:

```json
{
  "version": 1,
  "task_id": "TASK-20260310-0001",
  "title": "로그인 리팩토링",
  "slug": "login-refactor",
  "state": "planning",
  "created_at": "2026-03-10T09:00:00+09:00",
  "updated_at": "2026-03-10T09:00:03+09:00",
  "request": {
    "path": "REQUEST.md"
  },
  "plan": {
    "revision": 1,
    "approved": false,
    "path": "PLAN.md"
  },
  "implementation": {
    "iteration": 0,
    "workspace": null,
    "last_result": null
  },
  "review": {
    "iteration": 0,
    "last_verdict": null
  },
  "integration": {
    "applied": false,
    "base_branch": "main",
    "base_commit": null,
    "patch_path": null
  },
  "commit": {
    "status": "pending",
    "sha": null,
    "message_path": null
  },
  "lease": {
    "owner": null,
    "run_id": null,
    "heartbeat_at": null
  },
  "history": [
    {
      "state": "requests",
      "entered_at": "2026-03-10T09:00:00+09:00",
      "by": "human"
    }
  ],
  "errors": []
}
```

### 설계 원칙

- `state`는 **현재 디렉토리 위치와 일치**해야 한다
- `history`는 상태 변경 감사를 위해 유지한다
- `lease`는 **현재 누가 잡고 일하는지** 보여준다
- `workspace.path`는 task 디렉토리 바깥 `_runtime/workspaces/...` 를 가리킨다
- `integration.base_commit`는 integration 적용 시점 기준 base snapshot이다

---

## 락 전략

## 왜 락이 필요한가

동시에 여러 worker가 같은 task를 집으면 안 된다.  
또한 사람이 수동으로 디렉토리를 이동하는 순간과 worker가 자동 전이하는 순간이 충돌할 수 있다.

### 권장 방식

- **per-task lock file** 사용
- 위치: `.kanban-agent/_runtime/locks/{task_id}.lock`
- 라이브러리: `filelock`

### 중요한 이유

Task 디렉토리는 상태 전이 때 계속 이동한다.  
따라서 lock 파일을 task 디렉토리 안에 두면 lock 경로 자체가 바뀐다.  
락은 **움직이지 않는 stable path**에 둬야 한다.

### lease/heartbeat

OS-level lock만으로 충분하지 않은 경우를 대비해 `metadata.json`에 아래를 기록한다.

- `lease.owner`
- `lease.run_id`
- `lease.heartbeat_at`

#### 권장 정책
- worker는 10초마다 heartbeat 갱신
- `heartbeat_at` 이 60초 이상 stale이면 orphan candidate
- 서버 재기동 시 orphan recovery 수행

### atomic write / atomic move

- `metadata.json` 수정: `tmp` 파일 작성 후 `os.replace`
- state directory 이동: 같은 filesystem 안에서는 `rename` 사용
- 전제: 칸반 루트 디렉토리들은 **같은 filesystem** 위에 둔다

---

## watchfiles 사용 방식

`watchfiles`는 빠르고 비동기 파일 감시가 가능하며, 변경을 batch로 debounce 한다.  
하지만 rename/move 이벤트는 OS별로 다르게 보일 수 있다.

따라서 추천 패턴은:

1. `watchfiles.awatch(kanban_root)` 로 변경 감지
2. 변경 이벤트를 그대로 해석하지 말고
3. **짧게 debounce 후 전체 상태 디렉토리를 재스캔**
4. 재스캔 결과를 board snapshot으로 간주

즉:

- **파일시스템 전체 스캔 결과 = 진실**
- `watchfiles` = “재스캔할 타이밍을 알려주는 신호”

이렇게 하면 수동 rename, 자동 move, 복수 이벤트, editor temp file noise에 강해진다.

---

## FastAPI + SSE 구조

## 추천 구성

- FastAPI: REST + HTML 대시보드
- SSE: `/api/events`
- Board snapshot API: `/api/board`
- Task detail API: `/api/tasks/{task_id}`
- Health API: `/healthz`

### 권장 엔드포인트

#### `GET /api/board`
현재 칸반 전체 스냅샷.

응답 예시:

```json
{
  "generated_at": "2026-03-10T10:00:00+09:00",
  "columns": [
    {
      "state": "requests",
      "items": []
    },
    {
      "state": "planning",
      "items": [
        {
          "task_id": "TASK-20260310-0001",
          "title": "로그인 리팩토링",
          "updated_at": "2026-03-10T09:59:55+09:00",
          "iteration": 0,
          "has_error": false
        }
      ]
    }
  ]
}
```

#### `GET /api/tasks/{task_id}`
metadata + markdown 파일 목록 + 최근 로그 요약.

#### `GET /api/events`
SSE 스트림.  
이벤트 타입 예시:

- `board_snapshot`
- `task_updated`
- `task_moved`
- `worker_heartbeat`
- `worker_log`

### SSE 구현 라이브러리

FastAPI/Starlette 계열에서 SSE는 `sse-starlette` 의 `EventSourceResponse` 를 쓰는 것이 실용적이다.

### 대시보드 구현

처음에는 React까지 가지 말고 **단일 HTML + vanilla JS** 로 충분하다.

- 페이지 최초 로드 시 `/api/board` fetch
- 이후 `EventSource('/api/events')` 연결
- snapshot 또는 task update 이벤트를 받아 DOM 갱신

이 방식이면 deployment 부담이 거의 없다.

---

## Worker 구성

권장 worker는 4개다.

1. `PlanningWorker`
2. `ImplementerWorker`
3. `ReviewerWorker`
4. `CommitWorker`

### 1) PlanningWorker

#### 입력 상태
- `requests`

#### 동작
1. task lock 획득
2. canonical name / metadata 보정
3. `planning` 으로 이동
4. `REQUEST.md` 읽기
5. `opencode run --attach ... --agent fs-kanban-planner --format json`
6. 최종 assistant markdown을 `PLAN.md` 로 저장
7. `waiting-check-plans` 으로 이동

#### Planner는 read-only로 운영 추천
Planner agent는 파일을 직접 수정하지 말고 **markdown 결과만 stdout으로 반환**하게 하는 편이 안전하다.  
`PLAN.md` 저장은 Python 오케스트레이터가 직접 수행한다.

### 2) ImplementerWorker

#### 입력 상태
- `todos`

#### 동작
1. task lock 획득
2. isolated workspace 준비
3. `implementing` 이동
4. workspace cwd에서 `opencode run --attach ... --agent fs-kanban-implementer --format json`
5. raw run log 저장
6. 결과 요약을 `WORK-{n}.md` 로 저장
7. `waiting-reviews` 이동

### 3) ReviewerWorker

#### 입력 상태
- `waiting-reviews`

#### 동작
1. task lock 획득
2. `reviewing` 이동
3. review prompt 실행
4. `REVIEW-{n}.md` 생성
5. verdict가 `NEEDS_CHANGES` 이면 `todos` 로 이동
6. verdict가 `PASS` 이면 `completed-reviews` 로 이동

### 4) Human Verification

#### 입력 상태
- `completed-reviews`

#### 동작
1. 사람이 verification 시작
2. target repo clean 확인
3. workspace patch apply
4. `human-verifying` 이동
5. reject면 rollback 후 `todos`
6. approve면 commit 후 `done`

---

## workspace 전략: 왜 `worktree only` 대신 `clone + overlay` 인가

## 문제 인식

worktree는 “같은 git repo metadata를 공유하는 여러 checkout”에는 좋지만,  
**git에 없는 ignored/untracked 파일들까지 자동으로 보강해주지는 않는다.**

실제로 Flutter/모바일 프로젝트에서는 아래가 없으면 동작이 깨질 수 있다.

- `.env`
- `.fvm/`
- `android/local.properties`
- `.tool-versions`
- `.npmrc`
- 프로젝트 로컬 설정 파일들

### 추천 기본 전략: `clone-overlay`

#### 단계 1: local clone 생성
workspace용 repo를 별도로 만든다.

권장 예:
```bash
git clone --reference-if-able /path/to/main/repo --dissociate /path/to/main/repo /runtime/workspaces/TASK-.../repo
```

`--reference-if-able` 는 로컬 repo를 참조해 object 복사를 줄이고,  
`--dissociate` 로 clone 이후 source dependency를 끊는다.  
즉, `--shared` 의 장점 일부를 가져오되, source repo object 수명에 덜 민감하다.

#### 단계 2: 작업 브랜치 checkout
```bash
git checkout -b task/TASK-20260310-0001 origin/main
```

#### 단계 3: overlay manifest 적용
설정 파일에 아래 같은 whitelist를 둔다.

```yaml
workspace:
  overlay_copy:
    - .env
    - .env.local
    - .fvm
    - android/local.properties
    - .tool-versions
    - .npmrc
  overlay_symlink:
    - /Users/me/.pub-cache
    - /Users/me/.gradle
```

정책:
- **작고 민감한 파일**: copy
- **크고 재생성 비용이 큰 캐시**: symlink
- **build output** (`build/`, `dist/`, `.dart_tool/`) 는 기본적으로 복사하지 않음

### 장점

- worktree보다 독립성이 높다
- ignored/untracked 보강 가능
- branch / git history / diff 관리가 쉽다
- review / patch generation / re-run이 수월하다

### 단점

- disk usage 증가
- clone 준비 시간이 조금 든다

### 결론

이 프로젝트에는 **순수 worktree보다 clone-overlay가 더 맞다.**

---

## integration 전략

리뷰 통과 후 사람이 “원본 코드에서 실제 실행” 해볼 수 있어야 한다.  
즉, human verification 중에는 **target repo에 아직 commit되지 않은 형태로 변경이 반영**되어 있어야 한다.

### 권장 흐름

1. workspace repo에서 base branch 대비 patch 생성
2. 사람이 verification 시작을 누를 때 target repo가 clean한지 확인
3. target repo에서 patch 적용
4. 성공하면 `human-verifying`
5. 사람이 reject하면 rollback 후 `todos`
6. 사람이 approve하면 commit 후 `done`

### patch 적용 방법

권장:
```bash
git diff --binary <base_ref>...HEAD > patch.diff
git apply --3way --index patch.diff
```

`git apply --3way` 는 blob identity가 있는 patch를 바탕으로 3-way merge를 시도할 수 있다.  
충돌이 나면 implement 단계로 되돌려 rebase/재적용하게 하는 것이 낫다.

### target repo clean rule

반드시 전제 조건을 둔다.

- target repo에는 **이 시스템이 관리하지 않는 미완료 변경이 없어야 한다**
- 사람이 verification 하는 기준 작업 트리는 **전용/청결 상태**여야 한다

이 규칙이 없으면 rollback이 매우 복잡해진다.

### 실패 시 정책

integration apply 실패 시:
- `metadata.errors` 에 `integration-conflict` 기록
- task를 `todos` 로 되돌림
- implementer가 최신 base 기준으로 다시 맞추게 한다

---

## 원본 작업 트리와 사람이 테스트하는 환경

이 부분은 반드시 운영 규칙이 필요하다.

### 권장 운영 규칙

1. `repo_root` = 사람이 verification 하는 target 작업 트리
2. 이 작업 트리는 항상 clean 상태로 유지
3. 실제 구현은 `_runtime/workspaces/{task_id}/repo` 에서만 수행
4. 사람이 verification 시작 시에만 `repo_root` 에 patch 반영
5. reject면 rollback 후 `todos`, approve면 commit 후 `done`

이렇게 하면:

- 구현 중에는 격리 유지
- 리뷰 통과 후에만 실제 실행 환경 반영
- 사람이 보는 기준 repo가 명확함

---

## opencode 호출 방식

## 권장 공통 옵션

```bash
opencode run \
  --attach http://127.0.0.1:4096 \
  --agent fs-kanban-planner \
  --format json \
  "..."
```

### 왜 이 방식인가

- `opencode serve` 를 미리 띄워두면 매 실행마다 cold boot를 줄일 수 있다
- `--format json` 으로 run log를 저장/분석하기 좋다
- `--agent` 로 역할별 prompt contract를 고정할 수 있다

### Python 오케스트레이터 책임

- subprocess 실행
- cwd 지정
- timeout / cancellation
- stdout/stderr 수집
- 최종 assistant text 추출
- `PLAN.md`, `WORK-{n}.md`, `REVIEW-{n}.md`, `COMMIT.md` 저장
- 실패 시 metadata 갱신

---

## 실패 복구 / 재시작 전략

서버 재시작 시 아래를 수행한다.

### startup recovery

1. 모든 상태 디렉토리 스캔
2. `planning`, `implementing`, `reviewing` 상태 task 확인
3. lock 파일 실상태 + `lease.heartbeat_at` 검사
4. orphan 판단 시:
   - `planning` -> `requests` 또는 그대로 재시도 (configurable)
   - `implementing` -> `todos`
   - `reviewing` -> `waiting-reviews`

### 권장 기본값

- `planning` orphan → `requests`
- `implementing` orphan → `todos`
- `reviewing` orphan → `waiting-reviews`

이게 가장 사람이 이해하기 쉽다.

---

## observability / 감사 로그

반드시 남겨야 하는 것:

- 각 run의 raw JSON event log
- 최종 prompt hash 또는 prompt 파일 경로
- task state transition history
- worker heartbeat
- integration apply / commit 결과

### 저장 위치 권장

```text
.kanban-agent/_runtime/runs/{task_id}/
├─ planner-001.jsonl
├─ implementer-001.jsonl
├─ reviewer-001.jsonl
└─ committer-001.jsonl
```

task 폴더 안에는 **요약 로그만** 두고, 상세 로그는 `_runtime/runs` 로 빼도 된다.

---

## 최소 MVP 범위

처음부터 다 하지 말고 아래 순서가 좋다.

### MVP 1
- state dirs
- metadata.json
- lock
- scanner
- PlanningWorker
- ImplementerWorker
- ReviewerWorker
- FastAPI `/api/board`
- SSE `/api/events`
- 간단 HTML board

### MVP 2
- workspace clone-overlay
- integration patch apply
- CommitWorker
- orphan recovery
- 상세 task view

### MVP 3
- overlay manifest 고도화
- retry policy
- metrics
- auth
- multi-repo support

---

## 이 설계에서 특히 좋은 점

1. **사람 개입 지점이 명확**하다  
   - plan 승인
   - integration test 승인

2. **작업공간 격리**가 된다  
   - 구현 중 오염 방지
   - review 대상 분리

3. **파일시스템만 봐도 현재 상태를 알 수 있다**  
   - 디버깅 쉬움
   - 운영 단순

4. **OpenCode/oh-my-opencode 내부 workflow에 종속되지 않는다**
   - 도구는 바뀌어도 workflow는 유지된다

---

## 주의할 점

### 1) task 디렉토리 안에 workspace를 두지 말 것
상태 이동이 너무 비싸진다.

### 2) target repo는 clean 유지 규칙이 꼭 필요
사람이 동시에 다른 수동 작업을 하면 rollback이 꼬인다.

### 3) planner/reviewer는 read-only 출력형으로 만드는 것이 안전
문서 생성은 서버가 담당하는 편이 deterministic하다.

### 4) watch event를 그대로 믿지 말 것
재스캔 기반으로 board를 만들어야 OS 차이에 강하다.

### 5) 내부 oh-my 상태파일에 의존하지 말 것
외부 칸반 상태머신이 진실 소스가 되어야 한다.

---

## 최종 추천안 요약

### 채택
- Python 단일 앱
- FastAPI + SSE
- watchfiles + full rescan
- filelock 기반 per-task lock
- task 상태는 디렉토리 + metadata.json
- workspace는 `_runtime/workspaces`
- 구현 격리는 `clone-overlay`
- integration은 `git apply --3way`
- commit은 final state에서만

### 비채택
- 순수 worktree only
- oh-my-opencode 내부 상태파일을 칸반의 source of truth로 사용
- task 디렉토리 안에 workspace 포함
- review 통과 전에 원본 repo 반영

---

## 참고 자료

- OpenCode CLI / Agents / Commands / Rules
- watchfiles docs
- filelock docs
- sse-starlette docs
- git clone / git apply / git merge docs
- oh-my-opencode 공개 릴리스/이슈(동시성, worktree-aware planning 관련)
