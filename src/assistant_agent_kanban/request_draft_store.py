from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .config import AppConfig
from .request_drafting import RequestDraftPayload, RequestDraftTranscriptEntry


class StoredRequestDraftTranscriptEntry(BaseModel):
    role: Literal["user", "assistant"]
    content: str = ""
    field_updates: dict[str, str] = Field(default_factory=dict)


class StoredRequestDraft(BaseModel):
    draft_id: str
    title: str = ""
    goal: str = ""
    background: str = ""
    plan_auto_approve: bool = True
    scope: str = ""
    out_of_scope: str = ""
    constraints: str = ""
    references: str = ""
    acceptance_criteria: str = ""
    target_repo: str = ""
    base_branch: str = ""
    request_upload_token: str = ""
    active_tab: Literal["assistant", "fields"] = "assistant"
    request_draft_input: str = ""
    transcript: list[StoredRequestDraftTranscriptEntry] = Field(default_factory=list)

    def to_drafting_payload(self, *, message: str) -> RequestDraftPayload:
        return RequestDraftPayload(
            title=self.title,
            goal=self.goal,
            background=self.background,
            plan_auto_approve=self.plan_auto_approve,
            scope=self.scope,
            out_of_scope=self.out_of_scope,
            constraints=self.constraints,
            references=self.references,
            acceptance_criteria=self.acceptance_criteria,
            target_repo=self.target_repo,
            base_branch=self.base_branch,
            transcript=[
                RequestDraftTranscriptEntry(role=entry.role, content=entry.content)
                for entry in self.transcript
            ],
            message=message,
        )


class RequestDraftStore:
    def __init__(self, config: AppConfig):
        self._config = config

    def create(self, data: dict[str, object] | None = None) -> StoredRequestDraft:
        draft = StoredRequestDraft(draft_id=self._generate_draft_id())
        if data:
            draft = _merge_draft(draft, data)
        self.save(draft)
        return draft

    def load(self, draft_id: str) -> StoredRequestDraft:
        return StoredRequestDraft.model_validate_json(self._path(draft_id).read_text())

    def save(self, draft: StoredRequestDraft) -> StoredRequestDraft:
        path = self._path(draft.draft_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(draft.model_dump(mode="json"), indent=2) + "\n")
        os.replace(tmp_path, path)
        return draft

    def update(self, draft_id: str, data: dict[str, object]) -> StoredRequestDraft:
        draft = self.load(draft_id)
        updated = _merge_draft(draft, data)
        self.save(updated)
        return updated

    def delete(self, draft_id: str) -> None:
        try:
            self._path(draft_id).unlink()
        except FileNotFoundError:
            return

    def exists(self, draft_id: str) -> bool:
        return self._path(draft_id).exists()

    def _path(self, draft_id: str) -> Path:
        normalized = (draft_id or "").strip()
        if not normalized or normalized != Path(normalized).name or "." in normalized:
            raise ValueError("request draft id is invalid")
        return self._config.request_drafts_dir / f"{normalized}.json"

    def _generate_draft_id(self) -> str:
        while True:
            candidate = secrets.token_hex(8)
            if not self.exists(candidate):
                return candidate


def serialize_request_draft_transcript_markdown(draft: StoredRequestDraft, *, language_code: str) -> str:
    transcript = [entry for entry in draft.transcript if entry.role in {"user", "assistant"}]
    if not transcript:
        return ""
    user_label = "You" if language_code == "en" else "사용자"
    assistant_label = "Composer assistant" if language_code == "en" else "작성 도우미"
    suggested_updates_label = "Suggested updates" if language_code == "en" else "제안된 변경"
    clear_field_label = "(clear field)" if language_code == "en" else "(필드 비우기)"
    sections: list[str] = []
    for index, entry in enumerate(transcript, start=1):
        sections.append(f"## {user_label if entry.role == 'user' else assistant_label} {index}")
        sections.append("")
        sections.append(entry.content or "")
        if entry.role == "assistant" and entry.field_updates:
            sections.append("")
            sections.append(f"### {suggested_updates_label}")
            sections.append("")
            for field_name, value in entry.field_updates.items():
                sections.append(f"- **{_field_label(field_name, language_code=language_code)}**: {value if value != '' else clear_field_label}")
        sections.append("")
    return "\n".join(sections).strip()


def _normalize_update_data(data: dict[str, object]) -> dict[str, object]:
    normalized = dict(data)
    if "transcript" in normalized and isinstance(normalized["transcript"], list):
        normalized["transcript"] = [
            StoredRequestDraftTranscriptEntry.model_validate(item)
            for item in normalized["transcript"]
        ]
    return normalized


def _merge_draft(draft: StoredRequestDraft, data: dict[str, object]) -> StoredRequestDraft:
    return StoredRequestDraft.model_validate({
        **draft.model_dump(mode="json"),
        **_normalize_update_data(data),
    })


def _field_label(field_name: str, *, language_code: str) -> str:
    labels = {
        "en": {
            "title": "Title",
            "goal": "Goal",
            "background": "Background",
            "scope": "Scope",
            "out_of_scope": "Out of Scope",
            "constraints": "Constraints",
            "references": "References",
            "acceptance_criteria": "Acceptance Criteria",
            "target_repo": "Target Repo",
            "base_branch": "Base Branch",
        },
        "ko": {
            "title": "제목",
            "goal": "목표",
            "background": "배경",
            "scope": "범위",
            "out_of_scope": "범위 외",
            "constraints": "제약사항",
            "references": "참고자료",
            "acceptance_criteria": "승인 기준",
            "target_repo": "대상 저장소",
            "base_branch": "기준 브랜치",
        },
    }
    return labels.get(language_code, labels["en"]).get(field_name, field_name)
