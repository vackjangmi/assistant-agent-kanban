from __future__ import annotations

from pathlib import Path

from .config import PROJECT_ROOT


def resolve_safe_target_repo_root(target_repo_root: Path) -> Path:
    resolved = target_repo_root.expanduser().resolve()
    project_root = PROJECT_ROOT.resolve()
    if _paths_overlap(resolved, project_root):
        raise ValueError(
            f"target repo `{resolved}` overlaps with the orchestrator project root `{project_root}` and is blocked"
        )
    return resolved


def _paths_overlap(left: Path, right: Path) -> bool:
    return _is_same_or_child(left, right) or _is_same_or_child(right, left)


def _is_same_or_child(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False
