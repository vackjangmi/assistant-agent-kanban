from __future__ import annotations

from urllib import parse

from assistant_agent_kanban.slack_api import _slack_complete_upload_external


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

    result = _slack_complete_upload_external(
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
