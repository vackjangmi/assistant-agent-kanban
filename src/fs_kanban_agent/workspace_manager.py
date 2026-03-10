from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import AppConfig
from .models import TaskMetadata


class WorkspaceManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def prepare(self, metadata: TaskMetadata) -> Path:
        workspace_root = self.config.workspace.root or (self.config.kanban_root / "_runtime/workspaces")
        workspace_dir = workspace_root / metadata.task_id
        repo_dir = workspace_dir / "repo"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        if not repo_dir.exists():
            git_dir = self.config.repo_root / ".git"
            if git_dir.exists():
                clone = subprocess.run(["git", "clone", str(self.config.repo_root), str(repo_dir)], capture_output=True, text=True, check=False)
                if clone.returncode != 0:
                    raise RuntimeError(clone.stderr.strip() or "git clone failed")
                checkout = subprocess.run(["git", "-C", str(repo_dir), "checkout", "-b", f"task/{metadata.task_id.lower()}"], capture_output=True, text=True, check=False)
                if checkout.returncode != 0:
                    raise RuntimeError(checkout.stderr.strip() or "git checkout failed")
            else:
                shutil.copytree(self.config.repo_root, repo_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "ai-kanban"))
        self._apply_overlays(repo_dir)
        metadata.implementation.workspace = str(repo_dir)
        metadata.implementation.branch = f"task/{metadata.task_id.lower()}"
        return repo_dir

    def _apply_overlays(self, repo_dir: Path) -> None:
        for relative in self.config.workspace.overlay_copy:
            source = self.config.repo_root / relative
            target = repo_dir / relative
            if source.exists() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(source, target)
        for source_text in self.config.workspace.overlay_symlink:
            source = Path(source_text)
            target = repo_dir / source.name
            if source.exists() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(source)
