from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict

from .enums import TaskState


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkerLease(BaseModel):
    owner: str | None = None
    run_id: str | None = None
    heartbeat_at: datetime | None = None


class HistoryEntry(BaseModel):
    state: TaskState
    entered_at: datetime
    by: str
    note: str | None = None


class TaskErrorInfo(BaseModel):
    code: str
    message: str
    created_at: datetime = Field(default_factory=utc_now)


class PlanInfo(BaseModel):
    revision: int = 0
    approved: bool = False
    path: str | None = None
    resolved_model: str | None = None
    session_id: str | None = None
    last_run_tokens: int = 0
    session_tokens: int = 0


class ImplementationInfo(BaseModel):
    iteration: int = 0
    workspace: str | None = None
    branch: str | None = None
    last_result: str | None = None
    resolved_model: str | None = None
    session_id: str | None = None
    last_run_tokens: int = 0
    session_tokens: int = 0


class ReviewInfo(BaseModel):
    iteration: int = 0
    last_verdict: Literal["PASS", "NEEDS_CHANGES"] | None = None
    resolved_model: str | None = None
    session_id: str | None = None
    last_run_tokens: int = 0
    session_tokens: int = 0


class RetryGateInfo(BaseModel):
    reason: str | None = None
    consecutive_count: int = 0
    not_before: datetime | None = None


class IntegrationInfo(BaseModel):
    applied: bool = False
    base_branch: str = "main"
    base_commit: str | None = None
    patch_path: str | None = None
    applied_at: datetime | None = None
    original_branch: str | None = None
    review_branch: str | None = None
    final_branch_summary: str | None = None
    final_branch: str | None = None


class CommitInfo(BaseModel):
    status: str = "pending"
    sha: str | None = None
    review_sha: str | None = None
    message_path: str | None = None
    prepared_message: str | None = None


class RequestInfo(BaseModel):
    path: str = "REQUEST.md"
    language: str | None = None


class HumanVerificationInfo(BaseModel):
    note_path: str | None = None
    comments_path: str | None = None
    note_markdown: str = ""


class TargetRepoInfo(BaseModel):
    repo_root: str = "."
    base_branch: str = "main"


class TaskMetadata(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    version: int = 1
    task_id: str
    title: str
    slug: str
    state: TaskState
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    request: RequestInfo = Field(default_factory=RequestInfo)
    human_verification: HumanVerificationInfo = Field(default_factory=HumanVerificationInfo)
    target: TargetRepoInfo = Field(default_factory=TargetRepoInfo)
    plan: PlanInfo = Field(default_factory=PlanInfo)
    cycle: int = 0
    implementation: ImplementationInfo = Field(default_factory=ImplementationInfo)
    review: ReviewInfo = Field(default_factory=ReviewInfo)
    integration: IntegrationInfo = Field(default_factory=IntegrationInfo)
    commit: CommitInfo = Field(default_factory=CommitInfo)
    retry_gate: RetryGateInfo = Field(default_factory=RetryGateInfo)
    lease: WorkerLease = Field(default_factory=WorkerLease)
    history: list[HistoryEntry] = Field(default_factory=list)
    errors: list[TaskErrorInfo] = Field(default_factory=list)


class TaskSnapshot(BaseModel):
    task_id: str
    title: str
    state: TaskState
    path: str
    updated_at: datetime
    state_entered_at: datetime | None = None
    iteration: int
    has_error: bool
    active_model: str | None = None
    agent_status: Literal["active", "waiting", "idle"] = "idle"
    agent_owner: str | None = None
    agent_heartbeat_at: datetime | None = None
    target_repo_root: str = "."
    target_repo_label: str = "."
    base_branch: str = "main"
    final_branch: str | None = None
    total_duration_ms: int = 0
    current_state_duration_ms: int = 0


class BoardColumn(BaseModel):
    state: TaskState
    items: list[TaskSnapshot]


class BoardSnapshot(BaseModel):
    generated_at: datetime = Field(default_factory=utc_now)
    columns: list[BoardColumn]


class StageTimingSummary(BaseModel):
    state: TaskState
    total_duration_ms: int = 0
    latest_duration_ms: int = 0
    latest_entered_at: datetime | None = None
    attempt_count: int = 0
    is_current: bool = False


class StageTimingSegment(BaseModel):
    state: TaskState
    entered_at: datetime
    exited_at: datetime | None = None
    duration_ms: int = 0
    visit_index: int = 1
    is_current: bool = False


class TaskStageTiming(BaseModel):
    total_duration_ms: int = 0
    summaries: list[StageTimingSummary] = Field(default_factory=list)
    segments: list[StageTimingSegment] = Field(default_factory=list)


class TaskDetail(BaseModel):
    metadata: TaskMetadata
    task_path: str
    request_markdown_path: str
    markdown_files: list[str]
    json_files: list[str]
    log_files: list[str]
    changed_files: list[ChangedFileSummary] = Field(default_factory=list)
    stage_timing: TaskStageTiming = Field(default_factory=TaskStageTiming)
    human_review: HumanReviewState = Field(default_factory=lambda: HumanReviewState())
    agent_status: Literal["active", "waiting", "idle"] = "idle"


class RetrospectiveRecord(BaseModel):
    exists: bool = False
    created: bool = False
    can_create: bool = False
    task_ids: list[str] = Field(default_factory=list)
    target_repo_root: str = "."
    target_repo_label: str = "."
    base_branch: str = "main"
    committed_branch: str | None = None
    completion_mode: Literal["new-branch", "target-branch"] | None = None
    repo_relative_path: str | None = None
    artifact_filename: str | None = None
    content: str = ""
    resolved_model: str | None = None
    session_id: str | None = None
    total_tokens: int = 0
    commit_sha: str | None = None
    generated_at: datetime | None = None


class HumanLineCommentAnchor(BaseModel):
    path: str
    side: Literal["left", "right"]
    line_number: int = Field(ge=1)
    line_kind: Literal["context", "add", "remove"]
    hunk_header: str | None = None


class HumanLineComment(BaseModel):
    id: str
    anchor: HumanLineCommentAnchor
    body_markdown: str
    cycle: int | None = None
    author: str = "human"
    resolved: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
    editable: bool = True


class HumanLineCommentsArtifact(BaseModel):
    comments: list[HumanLineComment] = Field(default_factory=list)


class ChangedFileSummary(BaseModel):
    id: str
    path: str
    display_path: str
    previous_path: str | None = None
    change_type: Literal["added", "deleted", "modified", "renamed"]
    additions: int = 0
    deletions: int = 0
    hunk_count: int = 0
    is_binary: bool = False


class ChangedFileLine(BaseModel):
    kind: Literal["context", "add", "remove"]
    old_line_number: int | None = None
    new_line_number: int | None = None
    content: str = ""


class ChangedFileSide(BaseModel):
    kind: Literal["context", "add", "remove", "empty"]
    line_number: int | None = None
    content: str = ""


class ChangedFileRow(BaseModel):
    left: ChangedFileSide
    right: ChangedFileSide


class ChangedFileHunk(BaseModel):
    header: str
    old_start: int
    new_start: int
    unified_lines: list[ChangedFileLine] = Field(default_factory=list)
    rows: list[ChangedFileRow] = Field(default_factory=list)


class ChangedFileDetail(BaseModel):
    summary: ChangedFileSummary
    hunks: list[ChangedFileHunk] = Field(default_factory=list)
    comments: list[HumanLineComment] = Field(default_factory=list)


class HumanReviewState(BaseModel):
    note_path: str | None = None
    comments_path: str | None = None
    note_markdown: str = ""
    total_comment_count: int = 0
    unresolved_comment_count: int = 0
    historical_comment_count: int = 0


class TaskLogEntry(BaseModel):
    name: str
    path: str
    rendered_content: str | None = None
    debug_rendered_content: str | None = None
    updated_at: datetime


class TaskLogs(BaseModel):
    task_id: str
    entries: list[TaskLogEntry]


class RunResult(BaseModel):
    ok: bool
    returncode: int
    assistant_text: str = ""
    stdout: str = ""
    stderr: str = ""
    raw_events_path: str | None = None
    command: list[str] = Field(default_factory=list)
    resolved_model: str | None = None
    session_id: str | None = None
    total_tokens: int = 0


class TaskContext(BaseModel):
    metadata: TaskMetadata
    task_dir: Path
    state: TaskState
    model_config = ConfigDict(arbitrary_types_allowed=True)


class WorkerEvent(BaseModel):
    event: str
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
