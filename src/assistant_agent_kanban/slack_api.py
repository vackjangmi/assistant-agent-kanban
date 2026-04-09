from __future__ import annotations

import json
from urllib import parse
from urllib import error, request


SLACK_API_BASE_URL = "https://slack.com/api"
SLACK_API_TIMEOUT_SECONDS = 10


def slack_api_call(method: str, *, token: str, body: dict[str, object] | None = None) -> dict[str, object]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    req = request.Request(f"{SLACK_API_BASE_URL}/{method}", data=payload, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=SLACK_API_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except json.JSONDecodeError:
            return {"ok": False, "error": f"http_error:{exc.code}"}
    except error.URLError as exc:
        return {"ok": False, "error": f"network_error:{exc.reason}"}


def slack_error_message(payload: dict[str, object], *, fallback: str) -> str:
    error_code = payload.get("error")
    if isinstance(error_code, str) and error_code:
        return f"{fallback} ({error_code})"
    return fallback


def slack_upload_file_to_thread(
    *,
    token: str,
    channel_id: str,
    thread_ts: str,
    filename: str,
    title: str,
    content: bytes,
) -> dict[str, object]:
    start = slack_api_call(
        "files.getUploadURLExternal",
        token=token,
        body={
            "filename": filename,
            "length": len(content),
        },
    )
    if not start.get("ok"):
        return start
    upload_url = start.get("upload_url")
    file_id = start.get("file_id")
    if not isinstance(upload_url, str) or not upload_url or not isinstance(file_id, str) or not file_id:
        return {"ok": False, "error": "invalid_upload_url_response"}
    upload_result = _slack_upload_binary(upload_url=upload_url, filename=filename, content=content)
    if not upload_result.get("ok"):
        return upload_result
    return _slack_complete_upload_external(
        token=token,
        file_id=file_id,
        title=title,
        channel_id=channel_id,
        thread_ts=thread_ts,
    )


def _slack_complete_upload_external(
    *,
    token: str,
    file_id: str,
    title: str,
    channel_id: str,
    thread_ts: str,
) -> dict[str, object]:
    form_body = parse.urlencode(
        {
            "files": json.dumps([{"id": file_id, "title": title}]),
            "channel_id": channel_id,
            "thread_ts": thread_ts,
        }
    )
    req = request.Request(
        f"{SLACK_API_BASE_URL}/files.completeUploadExternal",
        data=form_body.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=SLACK_API_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except json.JSONDecodeError:
            return {"ok": False, "error": f"http_error:{exc.code}"}
    except error.URLError as exc:
        return {"ok": False, "error": f"network_error:{exc.reason}"}


def _slack_upload_binary(*, upload_url: str, filename: str, content: bytes) -> dict[str, object]:
    boundary = "----AssistantAgentKanbanSlackUpload"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="filename"\r\n\r\n{filename}\r\n'.encode("utf-8"),
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"),
            b"Content-Type: text/markdown; charset=utf-8\r\n\r\n",
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    req = request.Request(
        upload_url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=SLACK_API_TIMEOUT_SECONDS) as response:
            response.read()
            return {"ok": 200 <= response.status < 300}
    except error.HTTPError as exc:
        return {"ok": False, "error": f"upload_http_error:{exc.code}"}
    except error.URLError as exc:
        return {"ok": False, "error": f"upload_network_error:{exc.reason}"}
