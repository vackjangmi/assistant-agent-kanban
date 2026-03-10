from __future__ import annotations

from pathlib import Path

from fs_kanban_agent.main import main


def test_request_cli_creates_request_with_target_repo(tmp_path, capsys):
    kanban_root = tmp_path / "ai-kanban"
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()

    main(
        [
            "request",
            "my task",
            "--target-repo",
            str(target_repo),
            "--kanban-root",
            str(kanban_root),
            "--base-branch",
            "develop",
            "--body",
            "Do the thing.",
        ]
    )

    output = capsys.readouterr().out.strip()
    request_path = Path(output) / "REQUEST.md"
    content = request_path.read_text()

    assert request_path.exists()
    assert f"repo_root: {target_repo.resolve()}" in content
    assert "base_branch: develop" in content
    assert "## Goal" in content
    assert "Do the thing." in content
