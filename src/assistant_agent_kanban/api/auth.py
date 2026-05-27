from __future__ import annotations

import ipaddress
from collections.abc import Callable
from html import escape
from typing import Awaitable

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ..user_settings_store import AuthUser, UserSettingsStore


AUTH_EXEMPT_PATHS = {
    "/healthz",
    "/login",
    "/api/auth/bootstrap",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
}

LOCAL_ADMIN_BLOCKED_DETAIL = "local admin mode is only available from localhost"

LOCAL_ADMIN_BLOCKED_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Local Admin Mode - Assistant Agent Kanban</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f8fafc;
      --panel: #ffffff;
      --border: #dbe2ee;
      --text: #0f172a;
      --muted: #64748b;
      --accent: #4f46e5;
      --accent-soft: #eef2ff;
      --code-bg: #f1f5f9;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0f172a;
        --panel: #111827;
        --border: #263247;
        --text: #f8fafc;
        --muted: #94a3b8;
        --accent: #818cf8;
        --accent-soft: rgba(129, 140, 248, 0.16);
        --code-bg: rgba(15, 23, 42, 0.86);
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(680px, 100%);
      display: grid;
      gap: 18px;
      padding: 28px;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: 0 18px 50px rgba(15, 23, 42, 0.12);
    }
    .eyebrow {
      width: fit-content;
      padding: 5px 9px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    h1 {
      margin: 0;
      font-size: clamp(28px, 4vw, 40px);
      line-height: 1.08;
      letter-spacing: 0;
    }
    p {
      margin: 0;
      color: var(--muted);
      font-size: 16px;
      line-height: 1.6;
    }
    .steps {
      display: grid;
      gap: 10px;
      margin: 4px 0 0;
      padding: 0;
      list-style: none;
    }
    .steps li {
      padding: 12px 14px;
      border: 1px solid var(--border);
      border-radius: 8px;
      color: var(--muted);
      line-height: 1.5;
    }
    code {
      display: inline-block;
      max-width: 100%;
      padding: 2px 6px;
      border-radius: 5px;
      background: var(--code-bg);
      color: var(--text);
      overflow-wrap: anywhere;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 0.92em;
    }
    a {
      color: var(--accent);
      font-weight: 700;
      text-decoration: none;
    }
    a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">Remote access blocked</div>
    <h1>Assistant Agent Kanban is running in local admin mode.</h1>
    <p>This mode only allows browser sessions from localhost. The address you opened, <code>__REQUESTED_URL__</code>, is treated as a remote client.</p>
    <ul class="steps">
      <li>Open this dashboard on the server machine at <a href="__LOCAL_URL__">__LOCAL_URL__</a>.</li>
      <li>To use this dashboard from another device or IP address, create an admin user from localhost and enable remote use in Settings.</li>
    </ul>
  </main>
</body>
</html>
"""


def local_admin_user() -> AuthUser:
    return AuthUser(user_id="local", username="local", is_admin=True)


def current_user(request: Request) -> AuthUser:
    if not auth_is_required(request):
        return local_admin_user()
    user = getattr(request.state, "current_user", None)
    if isinstance(user, AuthUser):
        return user
    raise PermissionError("authentication required")


def current_user_or_none(request: Request) -> AuthUser | None:
    if not auth_is_required(request):
        return None
    user = getattr(request.state, "current_user", None)
    return user if isinstance(user, AuthUser) else None


def auth_is_required(request: Request) -> bool:
    configured = getattr(request.app.state.runtime.config, "auth", None)
    if configured is not None and configured.enabled:
        return True
    store = getattr(request.app.state, "user_settings_store", None)
    if store is None:
        return False
    return store.user_count() > 0


def install_auth_middleware(app, store: UserSettingsStore) -> None:
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        config = request.app.state.runtime.config
        if not auth_is_required(request):
            if not _is_loopback_request(request):
                return _local_admin_blocked_response(request)
            return await call_next(request)
        path = request.url.path
        if path in AUTH_EXEMPT_PATHS:
            return await call_next(request)
        token = request.cookies.get(config.auth.session_cookie_name)
        user = store.user_for_session(token)
        if user is not None:
            request.state.current_user = user
            return await call_next(request)
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"detail": "authentication required"}, status_code=401)


def _local_admin_blocked_response(request: Request) -> Response:
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(_render_local_admin_blocked_html(request), status_code=403)
    return JSONResponse({"detail": LOCAL_ADMIN_BLOCKED_DETAIL}, status_code=403)


def _render_local_admin_blocked_html(request: Request) -> str:
    requested_url = escape(str(request.url), quote=True)
    local_url = escape(_localhost_url_for_request(request), quote=True)
    return (
        LOCAL_ADMIN_BLOCKED_HTML.replace("__REQUESTED_URL__", requested_url)
        .replace("__LOCAL_URL__", local_url)
    )


def _localhost_url_for_request(request: Request) -> str:
    scheme = request.url.scheme
    port = request.url.port
    default_port = 443 if scheme == "https" else 80
    port_suffix = "" if port is None or port == default_port else f":{port}"
    return f"{scheme}://localhost{port_suffix}{request.url.path}"


def _is_loopback_request(request: Request) -> bool:
    client_host = request.client.host if request.client is not None else ""
    host_header = request.headers.get("host", "")
    return _is_loopback_host(client_host, allow_test_hosts=True) and (
        not host_header or _is_loopback_host(host_header, allow_test_hosts=True)
    )


def _is_loopback_host(value: str, *, allow_test_hosts: bool = False) -> bool:
    host = _extract_hostname(value)
    if not host:
        return False
    if host == "localhost":
        return True
    if allow_test_hosts and host in {"testclient", "testserver"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _extract_hostname(value: str) -> str:
    text = value.strip().lower().rstrip(".")
    if not text:
        return ""
    if text.startswith("["):
        end = text.find("]")
        if end != -1:
            return text[1:end]
    if text.count(":") == 1:
        return text.rsplit(":", 1)[0]
    return text
