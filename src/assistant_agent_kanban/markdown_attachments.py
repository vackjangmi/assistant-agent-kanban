from __future__ import annotations

import base64
import binascii
import mimetypes
from pathlib import Path
import re
import uuid

from .exceptions import TransitionError


ATTACHMENT_NAME_RE = re.compile(r"[^a-zA-Z0-9]+")
ALLOWED_ATTACHMENT_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
EMBEDDED_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<url>data:image/(?P<subtype>png|jpeg|jpg|gif|webp);base64,(?P<data>[^)]+))\)"
)


def store_attachment(task_dir: Path, upload_name: str, content_type: str | None, data: bytes) -> dict[str, str]:
    suffix = Path(upload_name).suffix.lower()
    if suffix not in ALLOWED_ATTACHMENT_SUFFIXES:
        raise TransitionError("only png, jpg, jpeg, gif, and webp attachments are supported")
    if content_type and not content_type.startswith("image/"):
        raise TransitionError("only image attachments are supported")
    attachments_dir = attachments_dir_for_task(task_dir, create=True)
    stem = ATTACHMENT_NAME_RE.sub("-", Path(upload_name).stem.lower()).strip("-") or "image"
    stored_name = f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"
    path = attachments_dir / stored_name
    path.write_bytes(data)
    return {
        "filename": stored_name,
        "content_type": content_type or mimetypes.guess_type(stored_name)[0] or "application/octet-stream",
        "relative_path": f"_attachments/{stored_name}",
        "url": f"/api/tasks/{task_dir.name}/attachments/{stored_name}",
    }


def normalize_markdown_attachments(task_dir: Path, content: str) -> str:
    def replace(match: re.Match[str]) -> str:
        alt_text = match.group("alt")
        subtype = match.group("subtype")
        raw_data = re.sub(r"\s+", "", match.group("data"))
        try:
            decoded = base64.b64decode(raw_data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise TransitionError("embedded image data is invalid") from exc
        suffix = ".jpg" if subtype == "jpeg" else f".{subtype}"
        upload_name = f"{alt_text.strip() or 'image'}{suffix}"
        saved = store_attachment(task_dir, upload_name, f"image/{subtype}", decoded)
        return f"![{alt_text}]({saved['relative_path']})"

    return EMBEDDED_IMAGE_RE.sub(replace, content)


def attachments_dir_for_task(task_dir: Path, *, create: bool = False) -> Path:
    path = task_dir / "_attachments"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    resolved_task_dir = task_dir.resolve()
    resolved = path.resolve()
    if resolved.parent != resolved_task_dir or resolved.name != "_attachments":
        raise TransitionError("attachments directory must stay inside the task directory")
    return resolved
