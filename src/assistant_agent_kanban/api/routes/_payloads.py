from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ...config import normalize_runtime_assistant
from ...language import normalize_runtime_language


class CompletedGroupOverridePayload(BaseModel):
    group: str | None = None


class CreateRequestPayload(BaseModel):
    title: str
    goal: str
    request_upload_token: str | None = None
    request_draft_id: str | None = None
    request_draft_markdown: str | None = None
    background: str | None = None
    plan_auto_approve: bool = True
    scope: str | None = None
    out_of_scope: str | None = None
    constraints: str | None = None
    references: str | None = None
    acceptance_criteria: str | None = None
    target_repo: str
    base_branch: str | None = None


class UpdateMarkdownPayload(BaseModel):
    content: str


class UpdateRequestDraftPayload(BaseModel):
    title: str | None = None
    goal: str | None = None
    background: str | None = None
    plan_auto_approve: bool | None = None
    scope: str | None = None
    out_of_scope: str | None = None
    constraints: str | None = None
    references: str | None = None
    acceptance_criteria: str | None = None
    target_repo: str | None = None
    base_branch: str | None = None
    request_upload_token: str | None = None
    active_tab: Literal["assistant", "fields"] | None = None
    request_draft_input: str | None = None


class CreateRequestDraftPayload(UpdateRequestDraftPayload):
    pass


class HumanVerificationPayload(BaseModel):
    note: str = ""


class HumanVerificationApprovePayload(BaseModel):
    completion_mode: Literal["new-branch", "target-branch"] = "new-branch"


class HumanReviewNotePayload(BaseModel):
    content: str = ""


class ReviewerQuestionPayload(BaseModel):
    question: str = ""


class RetrospectivePayload(BaseModel):
    target_repo_root: str
    base_branch: str
    comparison_branch: str | None = None


class RetrospectiveCreatePayload(RetrospectivePayload):
    completion_mode: Literal["new-branch", "target-branch"]


class CreateLineCommentPayload(BaseModel):
    path: str
    side: Literal["left", "right"]
    line_number: int = Field(ge=1)
    line_kind: Literal["context", "add", "remove"]
    hunk_header: str | None = None
    body: str = ""


class UpdateChangedFileViewedPayload(BaseModel):
    viewed: bool = False


class UpdateHumanQaChecklistItemPayload(BaseModel):
    checked: bool | None = None
    skipped: bool | None = None
    note: str | None = None


class ModelSettingsPayload(BaseModel):
    class RoleBackendsPayload(BaseModel):
        planner: str | None = None
        request_draft: str | None = None
        plan_approval: str | None = None
        implementer: str | None = None
        reviewer: str | None = None
        commit: str | None = None

        @field_validator("planner", "request_draft", "plan_approval", "implementer", "reviewer", "commit", mode="before")
        @classmethod
        def normalize_role_backend(cls, value: str | None) -> str | None:
            if value is None:
                return None
            normalized = normalize_runtime_assistant(value)
            if normalized is None:
                raise ValueError("role assistant must be OpenCode, Codex CLI, Gemini CLI, or Claude Code")
            return normalized

    language: str | None = None
    theme: Literal["light", "dark"] | None = None
    coding_assistant: str | None = None
    role_backends: RoleBackendsPayload | None = None
    worker_live_logs_enabled: bool | None = None
    planner_model: str | None = None
    planner_session_token_budget: int | None = Field(default=None, ge=1)
    planner_agent_count: int | None = Field(default=None, ge=1)
    request_draft_model: str | None = None
    plan_approval_model: str | None = None
    plan_approval_session_token_budget: int | None = Field(default=None, ge=1)
    implementer_model: str | None = None
    implementer_session_token_budget: int | None = Field(default=None, ge=1)
    implementer_agent_count: int | None = Field(default=None, ge=1)
    reviewer_model: str | None = None
    reviewer_session_token_budget: int | None = Field(default=None, ge=1)
    reviewer_agent_count: int | None = Field(default=None, ge=1)
    commit_model: str | None = None
    commit_session_token_budget: int | None = Field(default=None, ge=1)
    repo_discovery_root: str | None = None
    repo_discovery_max_depth: int | None = Field(default=None, ge=1)
    slack_enabled: bool | None = None
    slack_socket_mode_enabled: bool | None = None
    slack_bot_token: str | None = None
    slack_app_token: str | None = None
    slack_default_channel: str | None = None
    slack_app_mention_enabled: bool | None = None

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_runtime_language(value)
        if normalized is None:
            raise ValueError("language must be EN or KO")
        return normalized

    @field_validator("coding_assistant", mode="before")
    @classmethod
    def normalize_coding_assistant(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_runtime_assistant(value)
        if normalized is None:
            raise ValueError("coding assistant must be OpenCode, Codex CLI, Gemini CLI, or Claude Code")
        return normalized


class SlackSettingsTestPayload(BaseModel):
    slack_enabled: bool | None = None
    slack_socket_mode_enabled: bool | None = None
    slack_bot_token: str | None = None
    slack_app_token: str | None = None
    slack_default_channel: str | None = None
    slack_app_mention_enabled: bool | None = None


class SlackReceiveTestStartPayload(BaseModel):
    pass


class ResumeImplementerPayload(BaseModel):
    resume_mode: Literal["pinned", "current-settings"] | None = None
    message: str | None = None


class ResumeReviewerPayload(BaseModel):
    resume_mode: Literal["pinned", "current-settings"] | None = None
    message: str | None = None


class ResumeReviewLoopPayload(BaseModel):
    message: str | None = None


class ResumePlannerPayload(BaseModel):
    message: str | None = None
