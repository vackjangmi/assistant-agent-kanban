from __future__ import annotations

import logging
from urllib import parse

from assistant_agent_kanban.slack_api import _slack_complete_upload_external_form, _slack_get_upload_url_external_form, slack_upload_file_to_thread


def test_slack_complete_upload_external_uses_form_encoded_files(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeResponse:
        status = 200

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["data"] = req.data.decode("utf-8")
        captured["content_type"] = req.get_header("Content-type")
        captured["authorization"] = req.get_header("Authorization")
        return _FakeResponse()

    monkeypatch.setattr("assistant_agent_kanban.slack_api.request.urlopen", fake_urlopen)

    result = _slack_complete_upload_external_form(
        token="xoxb-test",
        file_id="F123",
        title="REVIEW-035.md",
        channel_id="C123",
        thread_ts="173.456",
    )

    assert result == {"ok": True}
    assert captured["url"] == "https://slack.com/api/files.completeUploadExternal"
    assert captured["content_type"] == "application/x-www-form-urlencoded; charset=utf-8"
    assert captured["authorization"] == "Bearer xoxb-test"
    payload = parse.parse_qs(str(captured["data"]))
    assert payload["channel_id"] == ["C123"]
    assert payload["thread_ts"] == ["173.456"]
    assert payload["files"] == ['[{"id": "F123", "title": "REVIEW-035.md"}]']


def test_slack_get_upload_url_external_uses_form_encoding(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeResponse:
        status = 200

        def read(self):
            return b'{"ok": true, "upload_url": "https://upload.test", "file_id": "F123"}'

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["data"] = req.data.decode("utf-8")
        captured["content_type"] = req.get_header("Content-type")
        captured["authorization"] = req.get_header("Authorization")
        return _FakeResponse()

    monkeypatch.setattr("assistant_agent_kanban.slack_api.request.urlopen", fake_urlopen)

    result = _slack_get_upload_url_external_form(token="xoxb-test", filename="REVIEW-035.md", length=123)

    assert result == {"ok": True, "upload_url": "https://upload.test", "file_id": "F123"}
    assert captured["url"] == "https://slack.com/api/files.getUploadURLExternal"
    assert captured["content_type"] == "application/x-www-form-urlencoded; charset=utf-8"
    assert captured["authorization"] == "Bearer xoxb-test"
    payload = parse.parse_qs(str(captured["data"]))
    assert payload["filename"] == ["REVIEW-035.md"]
    assert payload["length"] == ["123"]


def test_slack_upload_file_to_thread_retries_completion_with_json_on_invalid_arguments(monkeypatch):
    calls: list[str] = []
    captured_body: dict[str, object] = {}

    def fake_api_call(method: str, *, token: str, body=None):
        calls.append(method)
        if method == "files.getUploadURLExternal":
            return {"ok": True, "upload_url": "https://upload.test", "file_id": "F123"}
        if method == "files.completeUploadExternal":
            captured_body.update(body or {})
            return {"ok": True}
        raise AssertionError(f"unexpected method {method}")

    def fake_upload_binary(*, upload_url: str, filename: str, content: bytes):
        return {"ok": True}

    def fake_complete_form(*, token: str, file_id: str, title: str, channel_id: str, thread_ts: str):
        return {"ok": False, "error": "invalid_arguments"}

    monkeypatch.setattr("assistant_agent_kanban.slack_api.slack_api_call", fake_api_call)
    monkeypatch.setattr("assistant_agent_kanban.slack_api._slack_upload_binary", fake_upload_binary)
    monkeypatch.setattr(
        "assistant_agent_kanban.slack_api._slack_get_upload_url_external_form",
        lambda *, token, filename, length: {"ok": True, "upload_url": "https://upload.test", "file_id": "F123"},
    )
    monkeypatch.setattr("assistant_agent_kanban.slack_api._slack_complete_upload_external_form", fake_complete_form)

    result = slack_upload_file_to_thread(
        token="xoxb-test",
        channel_id="C123",
        thread_ts="173.456",
        filename="REVIEW-035.md",
        title="REVIEW-035.md",
        content=b"hello",
    )

    assert result == {"ok": True}
    assert calls == ["files.completeUploadExternal"]
    assert captured_body == {
        "files": [{"id": "F123", "title": "REVIEW-035.md"}],
        "channel_id": "C123",
        "thread_ts": "173.456",
    }


def test_slack_upload_file_to_thread_retries_get_upload_url_with_json_on_invalid_arguments(monkeypatch):
    calls: list[str] = []

    def fake_api_call(method: str, *, token: str, body=None):
        calls.append(method)
        if method == "files.getUploadURLExternal":
            assert body == {"filename": "REVIEW-035.md", "length": 5}
            return {"ok": True, "upload_url": "https://upload.test", "file_id": "F123"}
        if method == "files.completeUploadExternal":
            return {"ok": True}
        raise AssertionError(f"unexpected method {method}")

    def fake_upload_binary(*, upload_url: str, filename: str, content: bytes):
        return {"ok": True}

    def fake_get_form(*, token: str, filename: str, length: int):
        return {"ok": False, "error": "invalid_arguments"}

    monkeypatch.setattr("assistant_agent_kanban.slack_api.slack_api_call", fake_api_call)
    monkeypatch.setattr("assistant_agent_kanban.slack_api._slack_upload_binary", fake_upload_binary)
    monkeypatch.setattr("assistant_agent_kanban.slack_api._slack_get_upload_url_external_form", fake_get_form)
    monkeypatch.setattr(
        "assistant_agent_kanban.slack_api._slack_complete_upload_external_form",
        lambda *, token, file_id, title, channel_id, thread_ts: {"ok": True},
    )

    result = slack_upload_file_to_thread(
        token="xoxb-test",
        channel_id="C123",
        thread_ts="173.456",
        filename="REVIEW-035.md",
        title="REVIEW-035.md",
        content=b"hello",
    )

    assert result == {"ok": True}
    assert calls == ["files.getUploadURLExternal"]


def test_slack_upload_file_to_thread_logs_failing_stage(monkeypatch, caplog):
    def fake_api_call(method: str, *, token: str, body=None):
        if method == "files.getUploadURLExternal":
            return {"ok": False, "error": "invalid_arguments"}
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr("assistant_agent_kanban.slack_api.slack_api_call", fake_api_call)
    monkeypatch.setattr(
        "assistant_agent_kanban.slack_api._slack_get_upload_url_external_form",
        lambda *, token, filename, length: {"ok": False, "error": "invalid_arguments"},
    )

    with caplog.at_level(logging.WARNING):
        result = slack_upload_file_to_thread(
            token="xoxb-test",
            channel_id="C123",
            thread_ts="173.456",
            filename="REVIEW-035.md",
            title="REVIEW-035.md",
            content=b"hello",
        )

    assert result == {"ok": False, "error": "invalid_arguments"}
    assert "slack upload failed during getUploadURLExternal" in caplog.text
