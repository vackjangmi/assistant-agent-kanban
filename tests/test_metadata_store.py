from __future__ import annotations

from fs_kanban_agent.enums import TaskState
from fs_kanban_agent.metadata_store import MetadataStore, slugify


def test_metadata_store_bootstrap_and_load_round_trip(tmp_path):
    task_dir = tmp_path / "requests" / "task"
    task_dir.mkdir(parents=True)

    store = MetadataStore()
    created = store.bootstrap(
        task_dir,
        TaskState.REQUESTS,
        "TASK-0001",
        "Login Refactor",
        slugify("Login Refactor"),
        target_repo_root=str((tmp_path / "repo").resolve()),
        base_branch="main",
    )
    loaded = store.load(task_dir)

    assert created.task_id == loaded.task_id
    assert loaded.state == TaskState.REQUESTS
    assert loaded.slug == "login-refactor"
    assert loaded.cycle == 0
    assert loaded.target.base_branch == "main"
    assert (task_dir / "metadata.json").exists()
