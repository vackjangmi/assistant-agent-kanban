from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..auth import auth_is_required, current_user_or_none


ONBOARDING_VERSION = 1


class LoginPayload(BaseModel):
    username: str
    password: str


class BootstrapPayload(LoginPayload):
    pass


class CreateUserPayload(LoginPayload):
    is_admin: bool = False


class OnboardingPayload(BaseModel):
    completed: bool = True
    version: int = ONBOARDING_VERSION


LOGIN_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Assistant Agent Kanban - Login</title>
  <style>
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f7f7fb; color: #16181d; }
    form { width: min(360px, calc(100vw - 32px)); display: grid; gap: 12px; background: white; border: 1px solid #dddfea; border-radius: 8px; padding: 24px; box-shadow: 0 12px 36px rgba(20, 24, 40, 0.08); }
    h1 { margin: 0 0 4px; font-size: 22px; }
    p { margin: 0 0 8px; color: #636779; }
    label { display: grid; gap: 6px; font-size: 13px; font-weight: 600; }
    input { border: 1px solid #cfd3e1; border-radius: 6px; padding: 10px 12px; font: inherit; }
    button { border: 0; border-radius: 6px; padding: 10px 12px; background: #2563eb; color: white; font-weight: 700; cursor: pointer; }
    #error { min-height: 18px; color: #b42318; font-size: 13px; white-space: pre-wrap; }
  </style>
</head>
<body>
  <form id="login-form">
    <h1>Assistant Agent Kanban</h1>
    <p id="mode">Sign in to continue.</p>
    <label for="username">Username
      <input id="username" name="username" autocomplete="username" placeholder="Username" required>
    </label>
    <label for="password">Password
      <input id="password" name="password" type="password" autocomplete="current-password" placeholder="••••••••" required>
    </label>
    <button id="submit" type="submit">Sign in</button>
    <div id="error"></div>
  </form>
  <script>
    const form = document.getElementById('login-form');
    const mode = document.getElementById('mode');
    const button = document.getElementById('submit');
    const errorBox = document.getElementById('error');
    let bootstrapRequired = false;

    async function loadMode() {
      const response = await fetch('/api/auth/me');
      const data = await response.json();
      bootstrapRequired = Boolean(data.bootstrap_required);
      if (data.authenticated) location.href = '/';
      mode.textContent = bootstrapRequired ? 'Create the first admin account to get started.' : 'Sign in to continue.';
      button.textContent = bootstrapRequired ? 'Create admin' : 'Sign in';
    }
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      errorBox.textContent = '';
      button.disabled = true;
      try {
        const payload = {
          username: document.getElementById('username').value,
          password: document.getElementById('password').value,
        };
        const response = await fetch(bootstrapRequired ? '/api/auth/bootstrap' : '/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Login failed.');
        location.href = '/';
      } catch (error) {
        errorBox.textContent = error.message || 'Login failed.';
      } finally {
        button.disabled = false;
      }
    });
    loadMode().catch((error) => { errorBox.textContent = error.message || 'Failed to load login state.'; });
  </script>
</body>
</html>
"""


def register(router: APIRouter) -> None:
    @router.get("/login", response_class=HTMLResponse)
    async def login_page() -> str:
        return LOGIN_HTML

    @router.get("/api/auth/me")
    async def auth_me(request: Request):
        runtime = request.app.state.runtime
        store = request.app.state.user_settings_store
        effective_auth_enabled = auth_is_required(request)
        user = current_user_or_none(request)
        if user is None and effective_auth_enabled:
            user = store.user_for_session(request.cookies.get(runtime.config.auth.session_cookie_name))
        onboarding = None
        if user is not None and effective_auth_enabled:
            onboarding = _onboarding_payload(store.get_user_onboarding_state(user.user_id, current_version=ONBOARDING_VERSION))
        return {
            "enabled": effective_auth_enabled,
            "authenticated": user is not None and effective_auth_enabled,
            "bootstrap_required": store.user_count() == 0,
            "onboarding": onboarding,
            "user": None if user is None or not effective_auth_enabled else {
                "user_id": user.user_id,
                "username": user.username,
                "is_admin": user.is_admin,
            },
        }

    @router.post("/api/auth/bootstrap")
    async def bootstrap_admin(payload: BootstrapPayload, request: Request, response: Response):
        store = request.app.state.user_settings_store
        if store.user_count() != 0:
            raise HTTPException(status_code=409, detail="admin account already exists")
        try:
            user = store.create_user(payload.username, payload.password, is_admin=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _set_session_cookie(response, request, user)
        return {"created": True, "username": user.username, "user_id": user.user_id, "is_admin": user.is_admin}

    @router.post("/api/auth/login")
    async def login(payload: LoginPayload, request: Request, response: Response):
        store = request.app.state.user_settings_store
        if not auth_is_required(request):
            return {"authenticated": True, "username": "local"}
        user = store.authenticate(payload.username, payload.password)
        if user is None:
            raise HTTPException(status_code=401, detail="invalid username or password")
        _set_session_cookie(response, request, user)
        return {"authenticated": True, "username": user.username, "user_id": user.user_id, "is_admin": user.is_admin}

    @router.post("/api/auth/logout")
    async def logout(request: Request, response: Response):
        runtime = request.app.state.runtime
        store = request.app.state.user_settings_store
        cookie_name = runtime.config.auth.session_cookie_name
        store.delete_session(request.cookies.get(cookie_name))
        response.delete_cookie(cookie_name)
        return {"logged_out": True}

    @router.patch("/api/auth/onboarding")
    async def update_onboarding(payload: OnboardingPayload, request: Request):
        if not auth_is_required(request):
            return _onboarding_payload({"completed": payload.completed, "completed_at": None, "version": payload.version})
        user = current_user_or_none(request)
        if user is None:
            raise HTTPException(status_code=401, detail="authentication required")
        state = request.app.state.user_settings_store.update_user_onboarding_state(
            user.user_id,
            completed=payload.completed,
            version=payload.version,
        )
        return _onboarding_payload(state)

    @router.get("/api/auth/users")
    async def list_users(request: Request):
        _require_admin(request)
        users = request.app.state.user_settings_store.list_users()
        return {"users": [_user_payload(user) for user in users]}

    @router.post("/api/auth/users")
    async def create_user(payload: CreateUserPayload, request: Request):
        _require_admin(request)
        store = request.app.state.user_settings_store
        try:
            user = store.create_user(payload.username, payload.password, is_admin=payload.is_admin)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="username already exists") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _user_payload(user)

    @router.delete("/api/auth/users")
    async def delete_all_users(request: Request, response: Response):
        _require_admin(request)
        runtime = request.app.state.runtime
        store = request.app.state.user_settings_store
        deleted_count = store.delete_all_users()
        response.delete_cookie(runtime.config.auth.session_cookie_name)
        return {"deleted": True, "deleted_count": deleted_count}

    @router.delete("/api/auth/users/{user_id}")
    async def delete_user(user_id: str, request: Request):
        admin_user = _require_admin(request)
        if user_id == admin_user.user_id:
            raise HTTPException(status_code=409, detail="cannot delete the signed-in user")
        store = request.app.state.user_settings_store
        try:
            deleted = store.delete_user(user_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if deleted is None:
            raise HTTPException(status_code=404, detail="user not found")
        return {"deleted": True, "user": _user_payload(deleted)}


def _set_session_cookie(response: Response, request: Request, user) -> None:
    runtime = request.app.state.runtime
    store = request.app.state.user_settings_store
    token, expires_at = store.create_session(user)
    response.set_cookie(
        runtime.config.auth.session_cookie_name,
        token,
        expires=expires_at,
        httponly=True,
        samesite="lax",
        secure=False,
    )


def _require_admin(request: Request):
    if not auth_is_required(request):
        raise HTTPException(status_code=401, detail="create the first admin account before managing users")
    user = current_user_or_none(request)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin required")
    return user


def _user_payload(user) -> dict[str, object]:
    return {
        "user_id": user.user_id,
        "username": user.username,
        "is_admin": user.is_admin,
    }


def _onboarding_payload(state) -> dict[str, object]:
    if hasattr(state, "model_dump"):
        return state.model_dump(mode="json")
    return {
        "completed": bool(state.get("completed")),
        "completed_at": state.get("completed_at"),
        "version": int(state.get("version") or 0),
    }
