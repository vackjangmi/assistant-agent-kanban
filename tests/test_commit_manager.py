import pytest

from assistant_agent_kanban.commit_manager import CommitManager
from assistant_agent_kanban.exceptions import CommitError
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.scanner import KanbanScanner

from .conftest import create_request_task


def test_build_commit_message_uses_summary_input_without_changed_files_and_caps_body(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "summary-driven-commit", target_repo_root=repo_root)
    scanner = KanbanScanner(config, MetadataStore())
    task = scanner.scan()[0]

    summary_markdown = "\n".join(
        [
            "# Task Summary: summary-driven-commit",
            "",
            "## Overview",
            f"- Task ID: `{task.metadata.task_id}`",
            "- Branch summary: `summary-driven-commit`",
            "",
            "## Why / Keywords",
            "- Goal: Keep the final commit message aligned with the completion summary.",
            "- Plan summary: Build the final commit body from summary.md.",
            "- Review summary: Confirm the commit body skips file listings.",
            "- Human review summary: Keep the message easy to scan in git log.",
            "- Keywords: summary, commit, changed, files",
            "- Extra note 1",
            "- Extra note 2",
            "- Extra note 3",
            "- Extra note 4",
            "- Extra note 5",
            "- Extra note 6",
            "- Extra note 7",
            "- Extra note 8",
            "",
            "## Changed Files (2)",
            "- `src/foo.py` — modified (+3 / -1, hunks=1)",
            "- `tests/test_foo.py` — modified (+2 / -0, hunks=1)",
        ]
    )

    message = CommitManager().build_commit_message(task.task_dir, task.metadata, summary_markdown=summary_markdown)

    lines = message.splitlines()
    assert lines[0] == "feat: summary-driven-commit"
    assert lines[1] == ""
    assert lines[2:] == [
        "Goal: Keep the final commit message aligned with the completion summary.",
        "Plan: Build the final commit body from summary.md.",
        "Review: Confirm the commit body skips file listings.",
        "Human review: Keep the message easy to scan in git log.",
        f"Task: {task.metadata.task_id}",
        "Branch: summary-driven-commit",
    ]
    assert all("Changed Files" not in line for line in lines)
    assert all("src/foo.py" not in line for line in lines)
    assert all("Extra note" not in line for line in lines)
    assert len(lines[2:]) == 6


def test_build_commit_message_rejects_summary_without_required_sections(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "invalid-summary-commit", target_repo_root=repo_root)
    scanner = KanbanScanner(config, MetadataStore())
    task = scanner.scan()[0]

    summary_markdown = "\n".join(
        [
            "# Task Summary: invalid-summary-commit",
            "",
            "## Changed Files (1)",
            "- `src/foo.py` — modified (+1 / -0, hunks=1)",
        ]
    )

    with pytest.raises(CommitError, match="missing required section"):
        CommitManager().build_commit_message(task.task_dir, task.metadata, summary_markdown=summary_markdown)


def test_build_commit_message_rejects_summary_without_allowed_commit_lines(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "empty-summary-commit", target_repo_root=repo_root)
    scanner = KanbanScanner(config, MetadataStore())
    task = scanner.scan()[0]

    summary_markdown = "\n".join(
        [
            "# Task Summary: empty-summary-commit",
            "",
            "## Overview",
            "",
            "## Why / Keywords",
            "- Keywords: summary, commit",
            "- Extra note",
        ]
    )

    with pytest.raises(CommitError, match="did not contain any commit-ready lines"):
        CommitManager().build_commit_message(task.task_dir, task.metadata, summary_markdown=summary_markdown)


def test_build_commit_message_rejects_empty_summary_input(configured_paths):
    config, repo_root, _ = configured_paths
    create_request_task(config, "blank-summary-commit", target_repo_root=repo_root)
    scanner = KanbanScanner(config, MetadataStore())
    task = scanner.scan()[0]

    with pytest.raises(CommitError, match="missing required section"):
        CommitManager().build_commit_message(task.task_dir, task.metadata, summary_markdown="")
