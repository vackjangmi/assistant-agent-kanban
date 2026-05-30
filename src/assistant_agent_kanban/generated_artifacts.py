from __future__ import annotations

import subprocess
from pathlib import Path


GENERATED_ARTIFACT_EXCLUDE_PATTERNS: tuple[str, ...] = (
    ".dart_tool/",
    ".flutter-plugins",
    ".flutter-plugins-dependencies",
    ".packages",
    "build/",
    "coverage/",
    ".gradle/",
    "android/.gradle/",
    "android/local.properties",
    "ios/Pods/",
    "ios/build/",
    "ios/Flutter/ephemeral/",
    "ios/Flutter/Generated.xcconfig",
    "ios/Flutter/flutter_export_environment.sh",
    "macos/Pods/",
    "macos/build/",
    "macos/Flutter/ephemeral/",
    "macos/Flutter/Generated.xcconfig",
    "macos/Flutter/flutter_export_environment.sh",
)

_EXCLUDE_MARKER_BEGIN = "# BEGIN Assistant Agent Kanban generated artifacts"
_EXCLUDE_MARKER_END = "# END Assistant Agent Kanban generated artifacts"


def ensure_generated_artifact_excludes(repo_root: Path) -> None:
    """Ignore regenerated build outputs in a managed workspace git index."""

    git_path = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--git-path", "info/exclude"],
        capture_output=True,
        text=True,
        check=False,
    )
    if git_path.returncode != 0:
        return
    exclude_path = (repo_root / git_path.stdout.strip()).resolve()
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text() if exclude_path.exists() else ""
    if _EXCLUDE_MARKER_BEGIN in existing:
        return
    block = "\n".join(
        [
            "",
            _EXCLUDE_MARKER_BEGIN,
            *GENERATED_ARTIFACT_EXCLUDE_PATTERNS,
            _EXCLUDE_MARKER_END,
            "",
        ]
    )
    exclude_path.write_text(existing.rstrip() + block)


def is_generated_artifact_path(path: Path | str) -> bool:
    normalized = Path(path).as_posix().lstrip("/")
    for pattern in GENERATED_ARTIFACT_EXCLUDE_PATTERNS:
        directory_pattern = pattern.endswith("/")
        clean_pattern = pattern.rstrip("/")
        if directory_pattern:
            if normalized == clean_pattern or normalized.startswith(f"{clean_pattern}/"):
                return True
        elif normalized == clean_pattern:
            return True
    return False
