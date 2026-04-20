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


class TargetRepoDriftSnapshot(BaseModel):
    repo_root: str
    base_branch: str
    current_branch: str | None = None
    head_sha: str | None = None
    dirty: bool = False
    status_short: str = ""


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


def snapshot_target_repo_state(target_repo_root: Path, *, base_branch: str) -> TargetRepoDriftSnapshot:
    resolved_repo = resolve_safe_target_repo_root(target_repo_root)
    status_short = _read_status_short(resolved_repo)
    return TargetRepoDriftSnapshot(
        repo_root=str(resolved_repo),
        base_branch=base_branch,
        current_branch=_read_current_branch(resolved_repo),
        head_sha=_read_branch_head(resolved_repo, base_branch),
        dirty=bool(status_short.strip()),
        status_short=status_short,
    )


def describe_target_repo_head_drift(
    *,
    expected_branch: str | None,
    expected_head_sha: str | None,
    current_branch: str | None,
    current_head_sha: str | None,
) -> str | None:
    if expected_head_sha != current_head_sha:
        return f"target repo base branch HEAD changed from {expected_head_sha or '(unknown)'} to {current_head_sha or '(unknown)'}"
    return None


def describe_target_repo_dirty_drift(
    *,
    expected_dirty: bool,
    current_branch: str | None,
    current_dirty: bool,
    current_status_short: str,
) -> str | None:
    if expected_dirty or not current_dirty or _is_review_branch(current_branch):
        return None
    summary = current_status_short.splitlines()[0].strip() if current_status_short.strip() else "working tree is dirty"
    return f"target repo working tree became dirty on {current_branch or '(detached)'}: {summary}"


def is_review_branch(branch: str | None) -> bool:
    return _is_review_branch(branch)


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


def _read_head_sha(repo_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    head_sha = result.stdout.strip()
    return head_sha or None


def _read_branch_head(repo_root: Path, branch: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", branch],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    head_sha = result.stdout.strip()
    return head_sha or None


def _read_status_short(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--short"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _is_review_branch(branch: str | None) -> bool:
    return bool(branch and branch.startswith("review/"))


def _prioritize_current_branch(branches: list[str], current_branch: str | None) -> list[str]:
    if not current_branch or current_branch not in branches:
        return branches
    return [current_branch, *[branch for branch in branches if branch != current_branch]]
