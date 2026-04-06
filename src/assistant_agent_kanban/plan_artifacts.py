from __future__ import annotations

from dataclasses import dataclass

from .language import generation_language_code


@dataclass(frozen=True)
class PlanArtifactValidationResult:
    artifact_text: str
    missing_heading: str | None


def required_plan_heading_lines(request_language: str | None) -> list[str]:
    language_code = generation_language_code(request_language)
    return [f"## {heading}" for heading in _expected_plan_headings(language_code)]


def validate_plan_markdown(text: str, *, request_language: str | None) -> PlanArtifactValidationResult:
    artifact = _strip_outer_markdown_fence(text.strip())
    heading_lines = _heading_lines_outside_fences(artifact)
    cursor = 0
    for marker in required_plan_heading_lines(request_language):
        while cursor < len(heading_lines) and heading_lines[cursor] != marker:
            cursor += 1
        if cursor >= len(heading_lines):
            return PlanArtifactValidationResult(artifact_text=artifact, missing_heading=marker)
        cursor += 1
    return PlanArtifactValidationResult(artifact_text=artifact, missing_heading=None)


def _expected_plan_headings(language_code: str) -> tuple[str, ...]:
    if language_code == "ko":
        return (
            "요약",
            "범위",
            "범위 외",
            "파일 맵",
            "단계별 계획",
            "검증 계획",
            "승인 기준",
            "리스크",
            "열린 질문",
        )
    return (
        "Summary",
        "Scope",
        "Out of Scope",
        "File Map",
        "Step-by-step Plan",
        "Validation Plan",
        "Acceptance Criteria",
        "Risks",
        "Open Questions",
    )


def _strip_outer_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not (stripped.startswith("```") or stripped.startswith("~~~")):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3:
        return stripped
    opening = lines[0].strip().lower()
    if opening not in {"```", "```markdown", "```md", "~~~", "~~~markdown", "~~~md"}:
        return stripped
    closing = lines[-1].strip()
    if opening.startswith("```") and closing != "```":
        return stripped
    if opening.startswith("~~~") and closing != "~~~":
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _heading_lines_outside_fences(text: str) -> list[str]:
    headings: list[str] = []
    active_fence: str | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        fence_char = _fence_char(stripped)
        if active_fence is not None:
            if fence_char == active_fence:
                active_fence = None
            continue
        if fence_char is not None:
            active_fence = fence_char
            continue
        if raw_line.startswith("## "):
            headings.append(raw_line.rstrip())
    return headings


def _fence_char(line: str) -> str | None:
    if line.startswith("```"):
        return "`"
    if line.startswith("~~~"):
        return "~"
    return None
