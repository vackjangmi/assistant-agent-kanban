from __future__ import annotations

from pathlib import Path

from .config import PROJECT_ROOT, AppConfig


def discover_target_repos(config: AppConfig) -> list[str]:
    root = (config.repo_discovery.root or config.repo_root.expanduser().resolve().parent).expanduser().resolve()
    kanban_root = config.kanban_root.expanduser().resolve()
    max_depth = max(config.repo_discovery.max_depth, 1)
    results: list[str] = []
    seen: set[Path] = set()
    for path, depth in _walk_directories(root, max_depth):
        if path == PROJECT_ROOT or path.is_relative_to(PROJECT_ROOT):
            continue
        if path == kanban_root or path.is_relative_to(kanban_root):
            continue
        if path in seen:
            continue
        seen.add(path)
        results.append(str(path))
    return sorted(results)


def _walk_directories(root: Path, max_depth: int) -> list[tuple[Path, int]]:
    discovered: list[tuple[Path, int]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith('.'):
            continue
        discovered.append((child.resolve(), 1))
        if max_depth > 1:
            discovered.extend(_walk_nested(child.resolve(), 2, max_depth))
    return discovered


def _walk_nested(path: Path, depth: int, max_depth: int) -> list[tuple[Path, int]]:
    discovered: list[tuple[Path, int]] = []
    for child in sorted(path.iterdir()):
        if not child.is_dir() or child.name.startswith('.'):
            continue
        discovered.append((child.resolve(), depth))
        if depth < max_depth:
            discovered.extend(_walk_nested(child.resolve(), depth + 1, max_depth))
    return discovered
