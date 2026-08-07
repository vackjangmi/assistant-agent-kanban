from __future__ import annotations

import json
from pathlib import Path

from assistant_agent_kanban.api.app import create_app
from assistant_agent_kanban.request_drafting import RequestDraftPayload, RequestDraftSourceContext, build_request_drafting_prompt

from .conftest import FakeAdapter


def test_build_request_drafting_prompt_includes_discovered_baseline_references(configured_paths):
    config, repo_root, _ = configured_paths
    (repo_root / "AGENTS.md").write_text("# agent rules\n")
    (repo_root / "CLAUDE.md").write_text("# claude rules\n")

    prompt = build_request_drafting_prompt(
        config=config,
        payload=RequestDraftPayload(
            title="Draft request",
            references="docs/spec.md\nAGENTS.md",
            target_repo=str(repo_root),
            base_branch="main",
            message="Tighten the request.",
        ),
    )

    assert '"references": "docs/spec.md\\nAGENTS.md\\nCLAUDE.md"' in prompt
    assert 'Auto-discovered baseline references:\n[\n  "AGENTS.md",\n  "CLAUDE.md"\n]' in prompt


def test_build_request_drafting_prompt_includes_read_only_source_context(configured_paths):
    config, repo_root, _ = configured_paths

    prompt = build_request_drafting_prompt(
        config=config,
        payload=RequestDraftPayload(
            title="Follow-up draft",
            target_repo=str(repo_root),
            base_branch="main",
            source_task_id="source-task",
            source_context=RequestDraftSourceContext(
                task_id="source-task",
                title="Completed source",
                state="done",
                target_repo=str(repo_root),
                base_branch="main",
                request_markdown="# Completed source\nOriginal work",
                commit_markdown="feat: completed source",
                review_markdown="Verdict: PASS",
            ),
            message="Draft the follow-up.",
        ),
    )

    assert "Source completed task context:" in prompt
    assert "read-only completed-work context" in prompt
    assert "Draft only the new follow-up request" in prompt
    assert '"task_id": "source-task"' in prompt
    assert "Original work" in prompt


def test_api_request_draft_prompt_includes_discovered_baseline_references(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.auto_dispatch = False
    config.runtime.role_backends.request_draft = "codex"
    config.codex.request_draft_model = "gpt-5.4"
    (repo_root / "AGENTS.md").write_text("# agent rules\n")

    draft_adapter = FakeAdapter(
        [json.dumps({"reply": "Added baseline refs.", "field_updates": {}})],
        resolved_models=["gpt-5.4"],
    )
    app = create_app(
        config,
        draft_adapter,
        FakeAdapter(["impl"]),
        FakeAdapter(["Verdict: PASS"]),
        adapter_registry={"codex": draft_adapter},
    )

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        response = client.post(
            "/api/request-drafts",
            json={
                "title": "Composer drafting flow",
                "goal": "Add a draft assistant.",
                "target_repo": str(repo_root),
                "base_branch": "main",
                "message": "Please tighten the references.",
            },
        )

    assert response.status_code == 200
    prompt = str(draft_adapter.run_calls[0]["prompt"])
    assert '"references": "AGENTS.md"' in prompt
    assert 'Auto-discovered baseline references:\n[\n  "AGENTS.md"\n]' in prompt


def test_draft_request_reapplies_baseline_references_when_model_omits_them(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.role_backends.request_draft = "codex"
    (repo_root / "AGENTS.md").write_text("# agent rules\n")
    (repo_root / "CLAUDE.md").write_text("# claude rules\n")

    draft_adapter = FakeAdapter(
        [json.dumps({"reply": "Updated request.", "field_updates": {"goal": "Sharper goal."}})]
    )

    from assistant_agent_kanban.request_drafting import draft_request

    result = draft_request(
        config=config,
        adapter_registry={"codex": draft_adapter},
        payload=RequestDraftPayload(
            goal="Original goal",
            references="docs/spec.md",
            target_repo=str(repo_root),
            base_branch="main",
            message="Improve this request.",
        ),
    )

    assert result.field_updates["goal"] == "Sharper goal."
    assert result.field_updates["references"] == "docs/spec.md\nAGENTS.md\nCLAUDE.md"


def test_draft_request_runs_assistant_outside_target_repo(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.role_backends.request_draft = "codex"
    draft_adapter = FakeAdapter([json.dumps({"reply": "Updated request.", "field_updates": {}})])

    from assistant_agent_kanban.request_drafting import draft_request

    draft_request(
        config=config,
        adapter_registry={"codex": draft_adapter},
        payload=RequestDraftPayload(
            target_repo=str(repo_root),
            base_branch="main",
            message="Improve this request.",
        ),
    )

    cwd = draft_adapter.run_calls[0]["cwd"]
    assert isinstance(cwd, Path)
    assert cwd != repo_root.resolve()
    assert repo_root.resolve() not in cwd.parents


def test_draft_request_reapplies_baseline_references_when_model_replaces_references(configured_paths):
    config, repo_root, _ = configured_paths
    config.runtime.role_backends.request_draft = "codex"
    (repo_root / "AGENTS.md").write_text("# agent rules\n")

    draft_adapter = FakeAdapter(
        [json.dumps({"reply": "Updated references.", "field_updates": {"references": "docs/design.md"}})]
    )

    from assistant_agent_kanban.request_drafting import draft_request

    result = draft_request(
        config=config,
        adapter_registry={"codex": draft_adapter},
        payload=RequestDraftPayload(
            references="docs/spec.md",
            target_repo=str(repo_root),
            base_branch="main",
            message="Improve references.",
        ),
    )

    assert result.field_updates["references"] == "docs/design.md\nAGENTS.md"
