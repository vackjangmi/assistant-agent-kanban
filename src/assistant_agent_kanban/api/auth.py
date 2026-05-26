from __future__ import annotations

import ipaddress
from collections.abc import Callable
from typing import Awaitable

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from ..user_settings_store import AuthUser, UserSettingsStore


AUTH_EXEMPT_PATHS = {
    "/healthz",
    "/login",
    "/api/auth/bootstrap",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
}


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
                return JSONResponse(
                    {"detail": "local admin mode is only available from localhost"},
                    status_code=403,
                )
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
