from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from assistant_agent_kanban.commit_manager import CommitManager
from assistant_agent_kanban.locks import TaskLockManager
from assistant_agent_kanban.metadata_store import MetadataStore
from assistant_agent_kanban.scanner import KanbanScanner
from assistant_agent_kanban.exceptions import CommitError
from assistant_agent_kanban.services.retrospective_service import RetrospectiveService

from .conftest import FakeAdapter, create_request_task
from .test_human_verification_service import _task_ready_for_human_verification


def _done_task_for_retrospective(config, task_name: str, *, commit_adapter=None):
    create_request_task(config, task_name)

    def unique_workspace_change(cwd):
        (cwd / "app.txt").write_text(f"review me {task_name}\n")

    _, verification_service, completed = _task_ready_for_human_verification(config, workspace_side_effect=unique_workspace_change)
    verification_service.start(completed.metadata.task_id, by="human")
    verification_service.approve(completed.metadata.task_id, by="human", completion_mode="target-branch")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    service = RetrospectiveService(scanner, config, locks, CommitManager(), adapter=commit_adapter)
    return scanner.find_task(completed.metadata.task_id), service


def _done_task_for_retrospective_with_request(config, task_name: str, *, commit_adapter=None, language: str | None = None, body: str | None = None):
    create_request_task(config, task_name, language=language, body=body)

    def unique_workspace_change(cwd):
        (cwd / "app.txt").write_text(f"review me {task_name}\n")

    _, verification_service, completed = _task_ready_for_human_verification(config, workspace_side_effect=unique_workspace_change)
    verification_service.start(completed.metadata.task_id, by="human")
    verification_service.approve(completed.metadata.task_id, by="human", completion_mode="target-branch")
    metadata_store = MetadataStore()
    scanner = KanbanScanner(config, metadata_store)
    locks = TaskLockManager(config, metadata_store)
    service = RetrospectiveService(scanner, config, locks, CommitManager(), adapter=commit_adapter)
    return scanner.find_task(completed.metadata.task_id), service


def _create_comparison_branch(repo_root, branch_name: str = "retro-compare-source"):
    subprocess.run(["git", "-C", str(repo_root), "switch", "main"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo_root), "switch", "-c", branch_name], check=True, capture_output=True, text=True)
    (repo_root / "compare.txt").write_text("compare branch change\n")
    subprocess.run(["git", "-C", str(repo_root), "add", "compare.txt"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", "add compare branch file"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo_root), "switch", "main"], check=True, capture_output=True, text=True)
    return branch_name


def test_retrospective_service_creates_target_branch_retrospective(configured_paths):
    config, repo_root, _ = configured_paths
    adapter = FakeAdapter(["# Retrospective\n\n## Summary\nTarget branch retro\n"], resolved_models=["openai/gpt-5-commit"])
    done, service = _done_task_for_retrospective(config, "retro-target-task", commit_adapter=adapter)

    record = service.create(str(repo_root), "main", by="human", completion_mode="target-branch")

    assert record.exists is True
    assert record.created is True
    assert record.committed_branch == "main"
    assert record.resolved_model == "openai/gpt-5-commit"
    assert record.repo_relative_path is not None
    assert (repo_root / record.repo_relative_path).exists()
    assert (config.retrospectives_dir / service._repo_key(repo_root) / (record.artifact_filename or "")).exists()
    assert (done.task_dir / f"RETRO-{service._branch_slug('main')}.md").exists() is False
    current_branch = subprocess.run(["git", "-C", str(repo_root), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == "main"
    assert adapter.run_calls[0]["agent"] == config.opencode.commit_agent


def test_retrospective_service_inspects_existing_group_artifact(configured_paths):
    config, _, _ = configured_paths
    adapter = FakeAdapter(["# Retrospective\n\n## Summary\nExisting retro\n"])
    done, service = _done_task_for_retrospective(config, "retro-existing-task", commit_adapter=adapter)
    created = service.create(done.metadata.target.repo_root, done.metadata.target.base_branch, by="human", completion_mode="target-branch")

    inspected = service.inspect(done.metadata.target.repo_root, done.metadata.target.base_branch)

    assert inspected.exists is True
    assert inspected.created is False
    assert inspected.content == created.content
    assert inspected.commit_sha == created.commit_sha


def test_retrospective_service_groups_done_tasks_by_completed_group_override(configured_paths):
    config, repo_root, _ = configured_paths
    adapter = FakeAdapter(["# Retrospective\n\n## Summary\nOverride group\n"])
    done, service = _done_task_for_retrospective(config, "retro-group-override-task", commit_adapter=adapter)
    metadata_store = MetadataStore()
    done.metadata.completed_group_override = "release/2026"
    metadata_store.save(done.task_dir, done.metadata)

    record = service.inspect(str(repo_root), "release/2026")

    assert record.exists is False
    assert done.metadata.task_id in record.task_ids


def test_retrospective_service_creates_new_branch_when_requested(configured_paths):
    config, repo_root, _ = configured_paths
    adapter = FakeAdapter(["# Retrospective\n\n## Summary\nBranch retro\n"])
    done, service = _done_task_for_retrospective(config, "retro-branch-task", commit_adapter=adapter)

    record = service.create(str(repo_root), "main", by="human", completion_mode="new-branch")

    assert record.exists is True
    assert record.created is True
    assert record.committed_branch is not None
    assert record.committed_branch.startswith("retro/main-")
    current_branch = subprocess.run(["git", "-C", str(repo_root), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    assert current_branch == record.committed_branch


def test_retrospective_service_blocks_dirty_target_repo(configured_paths):
    config, repo_root, _ = configured_paths
    adapter = FakeAdapter(["# Retrospective\n\n## Summary\nDirty retro\n"])
    done, service = _done_task_for_retrospective(config, "retro-dirty-task", commit_adapter=adapter)
    (repo_root / "untracked.txt").write_text("dirty\n")

    with pytest.raises(CommitError, match="target repository must be clean"):
        service.create(str(repo_root), "main", by="human", completion_mode="target-branch")


def test_retrospective_service_rejects_mismatched_group_artifacts(configured_paths):
    config, repo_root, _ = configured_paths
    adapter = FakeAdapter([
        "# Retrospective\n\n## Summary\nGroup retro\n",
        "# Retrospective\n\n## Summary\nAnother retro\n",
    ])
    done_one, service = _done_task_for_retrospective(config, "retro-group-one", commit_adapter=adapter)
    done_two, _ = _done_task_for_retrospective(config, "retro-group-two", commit_adapter=adapter)

    created = service.create(str(repo_root), "main", by="human", completion_mode="target-branch")
    assert created.exists is True
    retro_path = config.retrospectives_dir / service._repo_key(repo_root) / (created.artifact_filename or "")
    retro_path.write_text("# Retrospective\n\n## Summary\nDrifted content\n")

    inspected = service.inspect(str(repo_root), "main")

    assert inspected.exists is True
    assert inspected.created is False
    assert inspected.content == "# Retrospective\n\n## Summary\nDrifted content\n"


def test_retrospective_service_ignores_task_local_legacy_retrospective_without_repo_path(configured_paths):
    config, repo_root, _ = configured_paths
    done, service = _done_task_for_retrospective(config, "retro-ignore-task-local")
    legacy_json_path = done.task_dir / f"RETRO-{service._branch_slug('main')}.json"
    legacy_markdown_path = done.task_dir / f"RETRO-{service._branch_slug('main')}.md"
    legacy_json_path.write_text(
        service.inspect(str(repo_root), "main").model_copy(
            update={
                "exists": True,
                "created": False,
                "can_create": True,
                "artifact_filename": legacy_markdown_path.name,
                "repo_relative_path": None,
                "content": "",
            }
        ).model_dump_json(indent=2)
    )
    legacy_markdown_path.write_text("# Retrospective\n\n## Summary\nTask local only\n")

    inspected = service.inspect(str(repo_root), "main")

    assert inspected.exists is False


def test_retrospective_service_reads_legacy_record_from_target_repo_path(configured_paths):
    config, repo_root, _ = configured_paths
    done, service = _done_task_for_retrospective(config, "retro-legacy-repo-path")
    legacy_json_path = done.task_dir / f"RETRO-{service._branch_slug('main')}.json"
    legacy_markdown_path = done.task_dir / f"RETRO-{service._branch_slug('main')}.md"
    repo_relative_path = Path("docs/kanban-agent/retrospectives/legacy/retro-main.md")
    (repo_root / repo_relative_path).parent.mkdir(parents=True, exist_ok=True)
    (repo_root / repo_relative_path).write_text("# Retrospective\n\n## Summary\nTarget repo legacy\n")
    legacy_json_path.write_text(
        service.inspect(str(repo_root), "main").model_copy(
            update={
                "exists": True,
                "created": False,
                "can_create": True,
                "artifact_filename": legacy_markdown_path.name,
                "repo_relative_path": repo_relative_path.as_posix(),
                "content": "",
            }
        ).model_dump_json(indent=2)
    )
    legacy_markdown_path.write_text("# Retrospective\n\n## Summary\nTask local shadow\n")

    inspected = service.inspect(str(repo_root), "main")

    assert inspected.exists is True
    assert inspected.content == "# Retrospective\n\n## Summary\nTarget repo legacy\n"


@pytest.mark.parametrize("repo_relative_path", ["../escape.md", "/tmp/escape.md", "docs/kanban-agent/retrospectives"])
def test_retrospective_service_ignores_unsafe_legacy_repo_paths(configured_paths, repo_relative_path):
    config, repo_root, _ = configured_paths
    done, service = _done_task_for_retrospective(config, f"retro-unsafe-{abs(hash(repo_relative_path))}")
    legacy_json_path = done.task_dir / f"RETRO-{service._branch_slug('main')}.json"
    legacy_markdown_path = done.task_dir / f"RETRO-{service._branch_slug('main')}.md"
    if repo_relative_path == "docs/kanban-agent/retrospectives":
        (repo_root / repo_relative_path).mkdir(parents=True, exist_ok=True)
    legacy_json_path.write_text(
        service.inspect(str(repo_root), "main").model_copy(
            update={
                "exists": True,
                "created": False,
                "can_create": True,
                "artifact_filename": legacy_markdown_path.name,
                "repo_relative_path": repo_relative_path,
                "content": "",
            }
        ).model_dump_json(indent=2)
    )
    legacy_markdown_path.write_text("# Retrospective\n\n## Summary\nUnsafe task local\n")

    inspected = service.inspect(str(repo_root), "main")

    assert inspected.exists is False


def test_retrospective_service_builds_korean_prompt_when_request_language_is_korean(configured_paths):
    class PromptCapturingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(["# 회고\n\n## 요약\n좋음\n"])
            self.prompts: list[str] = []

        def run(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            return super().run(**kwargs)

    config, repo_root, _ = configured_paths
    adapter = PromptCapturingAdapter()
    done, service = _done_task_for_retrospective_with_request(
        config,
        "retro-korean-task",
        commit_adapter=adapter,
        language="ko",
        body="한국어로 회고를 작성합니다.",
    )

    service.create(str(repo_root), "main", by="human", completion_mode="target-branch")

    prompt = adapter.prompts[0]
    assert "엔지니어링 회고를 마크다운으로 간결하게 작성하세요." in prompt
    assert "회고는 반드시 Korean로 작성하세요." in prompt
    assert "대상 저장소:" in prompt
    assert "기준 브랜치:" in prompt
    assert "완료된 작업:" in prompt


def test_retrospective_service_falls_back_to_english_for_unsupported_language(configured_paths):
    class PromptCapturingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(["# Retrospective\n\n## Summary\nGood\n"])
            self.prompts: list[str] = []

        def run(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            return super().run(**kwargs)

    config, repo_root, _ = configured_paths
    adapter = PromptCapturingAdapter()
    _, service = _done_task_for_retrospective_with_request(
        config,
        "retro-japanese-task",
        commit_adapter=adapter,
        language="ja",
        body="日本語の回顧です。",
    )

    service.create(str(repo_root), "main", by="human", completion_mode="target-branch")

    prompt = adapter.prompts[0]
    assert "Write a concise engineering retrospective in markdown." in prompt
    assert "Return the retrospective in English." in prompt


def test_retrospective_service_includes_comparison_branch_diff_summary(configured_paths):
    class PromptCapturingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(["# Retrospective\n\n## Summary\nCompared\n"])
            self.prompts: list[str] = []

        def run(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            return super().run(**kwargs)

    config, repo_root, _ = configured_paths
    comparison_branch = _create_comparison_branch(repo_root)
    adapter = PromptCapturingAdapter()
    _, service = _done_task_for_retrospective(config, "retro-compare-task", commit_adapter=adapter)

    record = service.create(str(repo_root), "main", comparison_branch, by="human", completion_mode="target-branch")

    prompt = adapter.prompts[0]
    assert record.comparison_branch == comparison_branch
    assert f"Comparison branch: {comparison_branch}" in prompt
    assert f"Git diff summary ({comparison_branch}..main):" in prompt
    assert "compare.txt" in prompt
