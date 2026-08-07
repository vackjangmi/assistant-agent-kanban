from __future__ import annotations

import pytest

from assistant_agent_kanban.request_draft_store import RequestDraftStore, serialize_request_draft_transcript_markdown


def test_request_draft_store_reuses_same_slack_thread_context(configured_paths):
    config, _, _ = configured_paths
    store = RequestDraftStore(config)

    first = store.load_or_create_for_slack_context(
        team_id="T123",
        user_id="U123",
        channel_id="C123",
        thread_ts="173.456",
        data={"title": "Draft from Slack"},
    )
    second = store.load_or_create_for_slack_context(
        team_id="T123",
        user_id="U123",
        channel_id="C123",
        thread_ts="173.456",
    )

    assert second.draft_id == first.draft_id
    assert second.title == "Draft from Slack"


def test_request_draft_store_separates_different_slack_threads(configured_paths):
    config, _, _ = configured_paths
    store = RequestDraftStore(config)

    first = store.load_or_create_for_slack_context(
        team_id="T123",
        user_id="U123",
        channel_id="C123",
        thread_ts="173.456",
    )
    second = store.load_or_create_for_slack_context(
        team_id="T123",
        user_id="U123",
        channel_id="C123",
        thread_ts="173.999",
    )

    assert second.draft_id != first.draft_id


def test_request_draft_store_backfills_missing_fields_from_saved_assistant_updates(configured_paths):
    config, _, _ = configured_paths
    store = RequestDraftStore(config)

    draft = store.create(
        {
            "title": "Manual title",
            "transcript": [
                {"role": "user", "content": "draft this"},
                {
                    "role": "assistant",
                    "content": "updated fields",
                    "field_updates": {
                        "title": "Suggested title",
                        "goal": "Suggested goal",
                        "background": "Suggested background",
                    },
                },
            ],
        }
    )

    loaded = store.load(draft.draft_id)

    assert loaded.title == "Manual title"
    assert loaded.goal == "Suggested goal"
    assert loaded.background == "Suggested background"


def test_request_draft_store_preserves_immutable_source_lineage(configured_paths):
    config, _, _ = configured_paths
    store = RequestDraftStore(config)

    draft = store.create({"source_task_id": "source-one", "source_title": "Source One"})
    updated = store.update(draft.draft_id, {"title": "Follow-up"})

    assert updated.source_task_id == "source-one"
    assert updated.title == "Follow-up"
    with pytest.raises(ValueError, match="source_task_id cannot be cleared"):
        store.update(draft.draft_id, {"source_task_id": ""})
    with pytest.raises(ValueError, match="source_task_id cannot be changed"):
        store.update(draft.draft_id, {"source_task_id": "source-two"})


def test_request_draft_store_rejects_source_lineage_attachment_to_existing_draft(configured_paths):
    config, _, _ = configured_paths
    store = RequestDraftStore(config)

    draft = store.create({"title": "Ordinary draft"})

    with pytest.raises(ValueError, match="source_task_id cannot be attached"):
        store.update(draft.draft_id, {"source_task_id": "source-one"})
    loaded = store.load(draft.draft_id)
    assert loaded.source_task_id == ""
    assert loaded.title == "Ordinary draft"


def test_request_draft_transcript_markdown_discloses_source_relation_without_transcript(configured_paths):
    config, _, _ = configured_paths
    store = RequestDraftStore(config)

    draft = store.create({"source_task_id": "source-one", "source_title": "Completed Source"})

    markdown = serialize_request_draft_transcript_markdown(draft, language_code="en")

    assert "## Source Completed Task" in markdown
    assert "`source-one`" in markdown
    assert "Completed Source" in markdown
