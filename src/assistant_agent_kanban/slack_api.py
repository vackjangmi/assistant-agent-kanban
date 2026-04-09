from __future__ import annotations

import json
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
