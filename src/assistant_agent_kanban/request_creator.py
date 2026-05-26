from __future__ import annotations

import base64
import binascii
import mimetypes
import re
import shutil
import uuid
import secrets
from pathlib import Path

from pydantic import BaseModel, Field
import yaml

from .config import AppConfig
from .enums import TaskState
from .language import runtime_language_code_to_request_language
from .metadata_store import MetadataStore, slugify
from .models import TaskRuntimePin
from .target_repo_guard import resolve_safe_target_repo_root


ATTACHMENT_NAME_RE = re.compile(r"[^a-zA-Z0-9]+")
ALLOWED_ATTACHMENT_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
EMBEDDED_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<url>data:image/(?P<subtype>png|jpeg|jpg|gif|webp);base64,(?P<data>[^)]+))\)"
)
REQUEST_UPLOAD_TOKEN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


class RequestTemplateData(BaseModel):
    title: str
    goal: str | None = None
    background: str | None = None
    plan_auto_approve: bool = True
    scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


def build_default_acceptance_criteria(*, language_code: str) -> list[str]:
    if language_code == "ko":
        return [
            "이 요청으로 추가하거나 변경한 코드의 모든 케이스를 테스트해야 하며, 그 변경 범위의 테스트 커버리지는 100%여야 한다.",
            "저장소 전체 커버리지 100%를 요구하는 뜻은 아니며, 전체 테스트 suite 는 작업 범위와 별개로 수행에 성공해야 한다.",
        ]
    return [
        "Add tests for every case introduced by the code added or changed for this request, and keep test coverage for that changed scope at 100%.",
        "This does not require 100% coverage across the entire repository; the full test suite must still pass separately from the changed-scope coverage target.",
    ]


def build_default_scope_sections(target_repo_root: str | Path, *, managed_docs_root: str = "docs/kanban-agent") -> tuple[list[str], list[str]]:
    return build_default_scope_sections_for_language(target_repo_root, language_code="en", managed_docs_root=managed_docs_root)


def build_default_scope_sections_for_language(target_repo_root: str | Path, *, language_code: str, managed_docs_root: str = "docs/kanban-agent") -> tuple[list[str], list[str]]:
    target_path = str(Path(target_repo_root).expanduser())
    docs_root = managed_docs_root.strip() or "docs/kanban-agent"
    if language_code == "ko":
        scope = [
            f"코드 변경 범위는 `{target_path}` 내부로 제한한다.",
            f"이 요청에 필요한 파일만 `{target_path}` 아래에서 수정한다.",
            f"테스트와 로컬 설정 변경도 `{target_path}` 범위 안에서만 수행한다.",
        ]
        out_of_scope = [
            f"`{target_path}` 밖의 파일은 수정하지 않는다.",
            f"`{docs_root}` 하위 문서는 요청에서 명시하지 않으면 수정하지 않는다.",
            "요청이나 승인된 계획에서 명시하지 않은 신규 파일은 생성하지 않는다.",
            "관련 없는 앱, 패키지, 워크스페이스 전체 설정은 변경하지 않는다.",
            "요청에서 명시하지 않으면 배포나 인프라 변경은 추가하지 않는다.",
        ]
        return scope, out_of_scope
    scope = [
        f"Limit code changes to `{target_path}`.",
        f"Modify only the files under `{target_path}` that are needed for this request.",
        f"Keep tests and local configuration changes scoped to `{target_path}`.",
    ]
    out_of_scope = [
        f"Do not modify files outside `{target_path}`.",
        f"Do not modify files under `{docs_root}` unless the request explicitly asks for it.",
        "Do not create new files unless the request or approved plan explicitly asks for them.",
        "Do not change unrelated apps, packages, or workspace-wide configuration.",
        "Do not add deployment or infrastructure changes unless the request explicitly asks for them.",
    ]
    return scope, out_of_scope


def create_request(
    config: AppConfig,
    *,
    template: RequestTemplateData,
    target_repo_root: Path,
    base_branch: str | None = None,
    request_language: str | None = None,
    request_upload_token: str | None = None,
    request_draft_markdown: str | None = None,
    slack_channel_id: str | None = None,
    slack_thread_ts: str | None = None,
    created_by_user_id: str | None = None,
    created_by_username: str | None = None,
    runtime_pin: TaskRuntimePin | None = None,
) -> Path:
    title = template.title.strip()
    goal = (template.goal or "").strip()
    if not title:
        raise ValueError("title is required")
    requests_dir = config.state_dir(TaskState.REQUESTS)
    task_dir = requests_dir / _generate_task_key(config.kanban_root)
    task_dir.mkdir(parents=True, exist_ok=False)
    try:
        resolved_repo = resolve_safe_target_repo_root(target_repo_root)
        request_language = request_language or runtime_language_code_to_request_language(config.runtime.language)
        finalized_uploads: dict[str, dict[str, str]] = {}
        normalized_template = template.model_copy(
            update={
                "goal": _normalize_request_markdown_field(task_dir, _finalize_request_uploads(config, task_dir, goal or None, request_upload_token, finalized_uploads)),
                "background": _normalize_request_markdown_field(task_dir, _finalize_request_uploads(config, task_dir, template.background, request_upload_token, finalized_uploads)),
                "scope": _normalize_request_markdown_field(task_dir, _finalize_request_uploads(config, task_dir, template.scope, request_upload_token, finalized_uploads)),
                "out_of_scope": _normalize_request_markdown_field(task_dir, _finalize_request_uploads(config, task_dir, template.out_of_scope, request_upload_token, finalized_uploads)),
                "constraints": _normalize_request_markdown_field(task_dir, _finalize_request_uploads(config, task_dir, template.constraints, request_upload_token, finalized_uploads)),
                "references": _normalize_request_markdown_field(task_dir, _finalize_request_uploads(config, task_dir, template.references, request_upload_token, finalized_uploads)),
                "acceptance_criteria": _normalize_request_markdown_field(task_dir, _finalize_request_uploads(config, task_dir, template.acceptance_criteria, request_upload_token, finalized_uploads)),
            }
        )
        metadata_store = MetadataStore()
        metadata = metadata_store.bootstrap(
            task_dir,
            TaskState.REQUESTS,
            task_dir.name,
            title,
            slugify(title),
            target_repo_root=str(resolved_repo),
            base_branch=base_branch or config.base_branch,
            request_language=request_language,
            request_plan_auto_approve=normalized_template.plan_auto_approve,
        )
        metadata.slack.channel = slack_channel_id
        metadata.slack.thread_ts = slack_thread_ts
        metadata.created_by_user_id = created_by_user_id
        metadata.created_by_username = created_by_username
        metadata.runtime_pin = runtime_pin
        metadata_store.save(task_dir, metadata)
        request_path = task_dir / "REQUEST.md"
        front_matter = yaml.safe_dump(
            {
                "title": title,
                "language": request_language,
                "plan_auto_approve": normalized_template.plan_auto_approve,
                "target": {
                    "repo_root": str(resolved_repo),
                    "base_branch": base_branch or config.base_branch,
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        lines = ["---", front_matter, "---", "", f"# {title}"]
        lines.extend(_render_request_sections(normalized_template.model_copy(update={"title": title}), language_code=request_language))
        request_path.write_text("\n".join(lines))
        draft_markdown = _normalize_request_markdown_field(
            task_dir,
            _finalize_request_uploads(config, task_dir, request_draft_markdown, request_upload_token, finalized_uploads),
        )
        if isinstance(draft_markdown, str) and draft_markdown.strip():
            (task_dir / "REQUEST-DRAFT.md").write_text(_render_request_draft_artifact(draft_markdown))
        _clear_request_uploads(config, request_upload_token)
        return task_dir
    except Exception:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise


def split_lines(value: str | None) -> list[str]:
    if value is None:
        return []
    return [line.strip().lstrip("- ").strip() for line in value.splitlines() if line.strip()]


def save_request_upload(config: AppConfig, upload_token: str, upload_name: str, content_type: str | None, data: bytes) -> dict[str, str]:
    token = _normalize_request_upload_token(upload_token)
    upload_dir = _request_upload_dir(config, token, create=True)
    saved = _store_attachment_in_dir(upload_dir, upload_name, content_type, data)
    saved["upload_token"] = token
    saved["url"] = f"/api/request-uploads/{token}/{saved['filename']}"
    saved["relative_path"] = saved["url"]
    return saved


def get_request_upload(config: AppConfig, upload_token: str, filename: str) -> tuple[Path, str]:
    token = _normalize_request_upload_token(upload_token)
    upload_dir = _request_upload_dir(config, token)
    path = (upload_dir / filename).resolve()
    if path.parent != upload_dir or not path.exists():
        raise ValueError("request upload not found")
    return path, mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def delete_request_uploads(config: AppConfig, upload_token: str) -> None:
    _clear_request_uploads(config, upload_token)


def _normalize_markdown_attachments(task_dir: Path, content: str | None) -> str | None:
    if not content or not content.strip():
        return content

    def replace(match: re.Match[str]) -> str:
        alt_text = match.group("alt")
        subtype = match.group("subtype")
        raw_data = re.sub(r"\s+", "", match.group("data"))
        try:
            decoded = base64.b64decode(raw_data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("embedded image data is invalid") from exc
        suffix = ".jpg" if subtype == "jpeg" else f".{subtype}"
        upload_name = f"{alt_text.strip() or 'image'}{suffix}"
        saved = _store_attachment(task_dir, upload_name, f"image/{subtype}", decoded)
        return f"![{alt_text}]({saved['relative_path']})"

    return EMBEDDED_IMAGE_RE.sub(replace, content)


def _normalize_request_markdown_field(task_dir: Path, content: str | list[str] | None) -> str | list[str] | None:
    if content is None:
        return None
    if isinstance(content, list):
        return [normalized for item in content if (normalized := _normalize_markdown_attachments(task_dir, item))]
    return _normalize_markdown_attachments(task_dir, content)


def _render_request_draft_artifact(content: str) -> str:
    body = content.strip()
    if body.startswith('# Request Draft Transcript'):
        body = body.removeprefix('# Request Draft Transcript').lstrip()
    return "\n".join(
        [
            "# Request Draft Transcript",
            "",
            "> Non-authoritative drafting context captured at request creation time.",
            "> `REQUEST.md` remains the only authoritative planner input.",
            "",
            body,
            "",
        ]
    )


def _finalize_request_uploads(
    config: AppConfig,
    task_dir: Path,
    content: str | list[str] | None,
    upload_token: str | None,
    finalized_uploads: dict[str, dict[str, str]] | None = None,
) -> str | list[str] | None:
    if not content or not upload_token:
        return content
    if isinstance(content, list):
        finalized_items: list[str] = []
        for item in content:
            finalized_item = _finalize_request_uploads(config, task_dir, item, upload_token, finalized_uploads)
            if isinstance(finalized_item, str) and finalized_item:
                finalized_items.append(finalized_item)
        return finalized_items
    if not content.strip():
        return content
    token = _normalize_request_upload_token(upload_token)
    upload_dir = _request_upload_dir(config, token)
    pattern = re.compile(rf"(?P<prefix>!\[[^\]]*\]\()/api/request-uploads/{re.escape(token)}/(?P<filename>[^)]+)(?P<suffix>\))")
    finalized: dict[str, dict[str, str]] = finalized_uploads if finalized_uploads is not None else {}

    def replace(match: re.Match[str]) -> str:
        filename = Path(match.group("filename")).name
        saved = finalized.get(filename)
        if saved is None:
            source = (upload_dir / filename).resolve()
            if source.parent != upload_dir or not source.exists():
                raise ValueError("request upload not found")
            saved = _move_attachment_into_task(task_dir, source)
            finalized[filename] = saved
        return f"{match.group('prefix')}{saved['relative_path']}{match.group('suffix')}"

    return pattern.sub(replace, content)


def _store_attachment(task_dir: Path, upload_name: str, content_type: str | None, data: bytes) -> dict[str, str]:
    return _store_attachment_in_dir(task_dir / "_attachments", upload_name, content_type, data)


def _store_attachment_in_dir(target_dir: Path, upload_name: str, content_type: str | None, data: bytes) -> dict[str, str]:
    suffix = Path(upload_name).suffix.lower()
    if suffix not in ALLOWED_ATTACHMENT_SUFFIXES:
        raise ValueError("only png, jpg, jpeg, gif, and webp attachments are supported")
    if content_type and not content_type.startswith("image/"):
        raise ValueError("only image attachments are supported")
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = ATTACHMENT_NAME_RE.sub("-", Path(upload_name).stem.lower()).strip("-") or "image"
    stored_name = f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"
    path = target_dir / stored_name
    path.write_bytes(data)
    return {
        "filename": stored_name,
        "content_type": content_type or mimetypes.guess_type(stored_name)[0] or "application/octet-stream",
        "relative_path": f"_attachments/{stored_name}",
        "url": str(path),
    }


def _move_attachment_into_task(task_dir: Path, source: Path) -> dict[str, str]:
    suffix = source.suffix.lower()
    if suffix not in ALLOWED_ATTACHMENT_SUFFIXES:
        raise ValueError("only png, jpg, jpeg, gif, and webp attachments are supported")
    attachments_dir = task_dir / "_attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)
    stem = ATTACHMENT_NAME_RE.sub("-", source.stem.lower()).strip("-") or "image"
    stored_name = f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"
    target = attachments_dir / stored_name
    source.replace(target)
    return {
        "filename": stored_name,
        "content_type": mimetypes.guess_type(stored_name)[0] or "application/octet-stream",
        "relative_path": f"_attachments/{stored_name}",
        "url": f"/api/tasks/{task_dir.name}/attachments/{stored_name}",
    }


def _request_upload_dir(config: AppConfig, upload_token: str, *, create: bool = False) -> Path:
    token = _normalize_request_upload_token(upload_token)
    root = config.request_uploads_dir.resolve()
    path = (root / token).resolve()
    if path.parent != root:
        raise ValueError("request upload token is invalid")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_request_upload_token(upload_token: str | None) -> str:
    token = (upload_token or "").strip()
    if not REQUEST_UPLOAD_TOKEN_RE.fullmatch(token):
        raise ValueError("request upload token is invalid")
    return token


def _clear_request_uploads(config: AppConfig, upload_token: str | None) -> None:
    if not upload_token:
        return
    try:
        upload_dir = _request_upload_dir(config, upload_token)
    except ValueError:
        return
    shutil.rmtree(upload_dir, ignore_errors=True)


def _render_request_sections(template: RequestTemplateData, *, language_code: str) -> list[str]:
    lines = [""]
    acceptance_criteria = _merge_acceptance_criteria(template.acceptance_criteria, language_code=language_code)
    if template.goal and template.goal.strip():
        lines.extend([f"## {_section_title('goal', language_code)}", template.goal.strip(), ""])
    if template.background and template.background.strip():
        lines.extend([f"## {_section_title('background', language_code)}", template.background.strip(), ""])
    lines.extend(_render_list_section(_section_title('scope', language_code), template.scope))
    lines.extend(_render_list_section(_section_title('out_of_scope', language_code), template.out_of_scope))
    lines.extend(_render_list_section(_section_title('constraints', language_code), template.constraints))
    lines.extend(_render_list_section(_section_title('references', language_code), template.references))
    lines.extend(_render_list_section(_section_title('acceptance_criteria', language_code), acceptance_criteria))
    return lines


def _render_list_section(title: str, items: list[str]) -> list[str]:
    if not items:
        return []
    lines = [f"## {title}"]
    lines.extend(f"- {item}" for item in items)
    lines.append("")
    return lines


def _section_title(key: str, language_code: str) -> str:
    localized = {
        "en": {
            "goal": "Goal",
            "background": "Background",
            "scope": "Scope",
            "out_of_scope": "Out of Scope",
            "constraints": "Constraints",
            "references": "References",
            "acceptance_criteria": "Acceptance Criteria",
        },
        "ko": {
            "goal": "목표",
            "background": "배경",
            "scope": "범위",
            "out_of_scope": "범위 외",
            "constraints": "제약사항",
            "references": "참고자료",
            "acceptance_criteria": "승인 기준",
        },
    }
    return localized.get(language_code, localized["en"])[key]


def _merge_acceptance_criteria(items: list[str], *, language_code: str) -> list[str]:
    defaults = build_default_acceptance_criteria(language_code=language_code)
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*defaults, *items]:
        normalized = item.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged


def _generate_task_key(kanban_root: Path) -> str:
    while True:
        candidate = secrets.token_hex(4)[:7]
        if not any(path.name == candidate for path in kanban_root.glob("*/**")):
            return candidate
