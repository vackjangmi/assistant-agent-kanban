from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from .config import AppConfig
from .target_repo_guard import resolve_safe_target_repo_root


class RepoBranchSnapshot(BaseModel):
    repo_root: str
    git_repository: bool
    branches: list[str] = Field(default_factory=list)
    current_branch: str | None = None
    suggested_base_branch: str | None = None


def describe_target_repo_branches(config: AppConfig, target_repo_root: Path) -> RepoBranchSnapshot:
    resolved_repo = resolve_safe_target_repo_root(target_repo_root)
    if not resolved_repo.exists():
        raise ValueError(f"target repo `{resolved_repo}` does not exist")
    if not resolved_repo.is_dir():
        raise ValueError(f"target repo `{resolved_repo}` is not a directory")
    if not _is_git_repository(resolved_repo):
        return RepoBranchSnapshot(
            repo_root=str(resolved_repo),
            git_repository=False,
            branches=[],
            current_branch=None,
            suggested_base_branch=config.base_branch,
        )

    branches = _list_local_branches(resolved_repo)
    current_branch = _read_current_branch(resolved_repo)
    ordered_branches = _prioritize_current_branch(branches, current_branch)
    suggested_base_branch = current_branch or (ordered_branches[0] if ordered_branches else config.base_branch)
    return RepoBranchSnapshot(
        repo_root=str(resolved_repo),
        git_repository=True,
        branches=ordered_branches,
        current_branch=current_branch,
        suggested_base_branch=suggested_base_branch,
    )


def _is_git_repository(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _list_local_branches(repo_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/heads/"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for line in result.stdout.splitlines():
        branch = line.strip()
        if not branch or branch in seen:
            continue
        seen.add(branch)
        ordered.append(branch)
    return ordered


def _read_current_branch(repo_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def _prioritize_current_branch(branches: list[str], current_branch: str | None) -> list[str]:
    if not current_branch or current_branch not in branches:
        return branches
    return [current_branch, *[branch for branch in branches if branch != current_branch]]
