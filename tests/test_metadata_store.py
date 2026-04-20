from __future__ import annotations

from assistant_agent_kanban.enums import TaskState
from assistant_agent_kanban.metadata_store import MetadataStore, slugify
from assistant_agent_kanban.models import TargetRepoBaselineInfo


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
    assert loaded.completed_group_override is None
    assert (task_dir / "metadata.json").exists()


def test_metadata_store_round_trips_target_repo_baseline(tmp_path):
    task_dir = tmp_path / "requests" / "task"
    task_dir.mkdir(parents=True)

    store = MetadataStore()
    created = store.bootstrap(
        task_dir,
        TaskState.REQUESTS,
        "TASK-0002",
        "Repo Drift Guard",
        slugify("Repo Drift Guard"),
        target_repo_root=str((tmp_path / "repo").resolve()),
        base_branch="main",
    )
    created.implementation.target_repo_baseline = TargetRepoBaselineInfo(
        repo_root=str((tmp_path / "repo").resolve()),
        base_branch="main",
        current_branch="main",
        head_sha="abc123",
        dirty=True,
        status_short=" M app.txt",
    )
    store.save(task_dir, created)

    loaded = store.load(task_dir)

    assert loaded.implementation.target_repo_baseline is not None
    assert loaded.implementation.target_repo_baseline.base_branch == "main"
    assert loaded.implementation.target_repo_baseline.head_sha == "abc123"
    assert loaded.implementation.target_repo_baseline.dirty is True
    assert loaded.implementation.target_repo_baseline.status_short == " M app.txt"
