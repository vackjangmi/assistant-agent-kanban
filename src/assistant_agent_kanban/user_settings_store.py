from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from pydantic import BaseModel, Field, SecretStr

from .config import AppConfig
from .security import SecretBox, generate_session_token, hash_password, hash_session_token, mask_secret, verify_password


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _secret_value(value: SecretStr | str | None) -> str | None:
    if value is None:
        return None
    text = value.get_secret_value() if isinstance(value, SecretStr) else value
    return text.strip() or None


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    username: str
    is_admin: bool

    @property
    def actor(self) -> str:
        return f"user:{self.username}"


class RoleBackendSettings(BaseModel):
    planner: str | None = None
    request_draft: str | None = None
    plan_approval: str | None = None
    implementer: str | None = None
    reviewer: str | None = None
    commit: str | None = None


class RuntimePreferenceSettings(BaseModel):
    language: str | None = None
    theme: str | None = None
    coding_assistant: str | None = None
    role_backends: RoleBackendSettings = Field(default_factory=RoleBackendSettings)
    planner_model: str | None = None
    request_draft_model: str | None = None
    plan_approval_model: str | None = None
    implementer_model: str | None = None
    reviewer_model: str | None = None
    commit_model: str | None = None
    planner_session_token_budget: int | None = None
    plan_approval_session_token_budget: int | None = None
    implementer_session_token_budget: int | None = None
    reviewer_session_token_budget: int | None = None
    commit_session_token_budget: int | None = None
    slack_enabled: bool | None = None
    slack_socket_mode_enabled: bool | None = None
    slack_default_channel: str | None = None
    slack_default_channel_display: str | None = None
    slack_app_mention_enabled: bool | None = None


class UserSettings(BaseModel):
    runtime: RuntimePreferenceSettings = Field(default_factory=RuntimePreferenceSettings)
    git_token_username: str | None = None
    git_token_configured: bool = False
    git_token_masked: str | None = None
    slack_bot_token_configured: bool = False
    slack_bot_token_masked: str | None = None
    slack_app_token_configured: bool = False
    slack_app_token_masked: str | None = None


class UserOnboardingState(BaseModel):
    completed: bool = False
    completed_at: str | None = None
    version: int = 0


class UserSecretUpdate(BaseModel):
    git_token: str | None = None
    git_token_username: str | None = None
    slack_bot_token: str | None = None
    slack_app_token: str | None = None


class ProjectSettings(BaseModel):
    repo_root: str
    runtime: RuntimePreferenceSettings = Field(default_factory=RuntimePreferenceSettings)
    git_remote_name: str | None = None
    slack_bot_token: SecretStr | None = None
    slack_bot_token_configured: bool = False
    slack_bot_token_masked: str | None = None
    slack_app_token: SecretStr | None = None
    slack_app_token_configured: bool = False
    slack_app_token_masked: str | None = None
    review_branch_push_enabled: bool | None = None
    review_branch_require_push_success: bool | None = None
    review_branch_delete_on_cleanup: bool | None = None


class UserSettingsStore:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.db_path = config.app_database_path.expanduser().resolve()
        self.secret_box = SecretBox(config.encryption_key_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def user_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("select count(*) as count from users").fetchone()
        return int(row["count"])

    def list_users(self) -> list[AuthUser]:
        with self._connect() as conn:
            rows = conn.execute("select user_id, username, is_admin from users order by username").fetchall()
        return [AuthUser(user_id=str(row["user_id"]), username=str(row["username"]), is_admin=bool(row["is_admin"])) for row in rows]

    def get_user(self, user_id: str) -> AuthUser | None:
        with self._connect() as conn:
            row = conn.execute("select user_id, username, is_admin from users where user_id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        return AuthUser(user_id=str(row["user_id"]), username=str(row["username"]), is_admin=bool(row["is_admin"]))

    def create_user(self, username: str, password: str, *, is_admin: bool = False) -> AuthUser:
        normalized = self._normalize_username(username)
        user_id = uuid.uuid4().hex
        now = _iso(_utc_now())
        with self._connect() as conn:
            conn.execute(
                "insert into users (user_id, username, password_hash, is_admin, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
                (user_id, normalized, hash_password(password), 1 if is_admin else 0, now, now),
            )
            conn.commit()
        return AuthUser(user_id=user_id, username=normalized, is_admin=is_admin)

    def delete_user(self, user_id: str) -> AuthUser | None:
        with self._connect() as conn:
            row = conn.execute("select user_id, username, is_admin from users where user_id = ?", (user_id,)).fetchone()
            if row is None:
                return None
            user = AuthUser(user_id=str(row["user_id"]), username=str(row["username"]), is_admin=bool(row["is_admin"]))
            if user.is_admin:
                admin_count = conn.execute("select count(*) as count from users where is_admin = 1").fetchone()
                if int(admin_count["count"]) <= 1:
                    raise ValueError("cannot delete the last admin")
            conn.execute("delete from sessions where user_id = ?", (user_id,))
            conn.execute("delete from user_settings where user_id = ?", (user_id,))
            conn.execute("delete from users where user_id = ?", (user_id,))
            conn.commit()
        return user

    def delete_all_users(self) -> int:
        with self._connect() as conn:
            row = conn.execute("select count(*) as count from users").fetchone()
            deleted_count = int(row["count"])
            conn.execute("delete from sessions")
            conn.execute("delete from user_settings")
            conn.execute("delete from users")
            conn.commit()
        return deleted_count

    def authenticate(self, username: str, password: str) -> AuthUser | None:
        normalized = self._normalize_username(username)
        with self._connect() as conn:
            row = conn.execute("select * from users where username = ?", (normalized,)).fetchone()
        if row is None or not verify_password(password, str(row["password_hash"])):
            return None
        return AuthUser(user_id=str(row["user_id"]), username=str(row["username"]), is_admin=bool(row["is_admin"]))

    def create_session(self, user: AuthUser) -> tuple[str, datetime]:
        token = generate_session_token()
        expires_at = _utc_now() + timedelta(seconds=self.config.auth.session_ttl_seconds)
        now = _iso(_utc_now())
        with self._connect() as conn:
            conn.execute(
                "insert into sessions (token_hash, user_id, created_at, expires_at) values (?, ?, ?, ?)",
                (hash_session_token(token), user.user_id, now, _iso(expires_at)),
            )
            conn.commit()
        return token, expires_at

    def user_for_session(self, token: str | None) -> AuthUser | None:
        if not token:
            return None
        now = _utc_now()
        with self._connect() as conn:
            row = conn.execute(
                """
                select users.user_id, users.username, users.is_admin, sessions.expires_at
                from sessions
                join users on users.user_id = sessions.user_id
                where sessions.token_hash = ?
                """,
                (hash_session_token(token),),
            ).fetchone()
        if row is None:
            return None
        expires_at = _parse_iso(str(row["expires_at"]))
        if expires_at is None or expires_at <= now:
            self.delete_session(token)
            return None
        return AuthUser(user_id=str(row["user_id"]), username=str(row["username"]), is_admin=bool(row["is_admin"]))

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self._connect() as conn:
            conn.execute("delete from sessions where token_hash = ?", (hash_session_token(token),))
            conn.commit()

    def get_user_onboarding_state(self, user_id: str, *, current_version: int = 1) -> UserOnboardingState:
        raw = self._raw_user_settings(user_id)
        completed_at = raw.get("onboarding_completed_at")
        version = int(raw.get("onboarding_version") or 0)
        completed = bool(completed_at and version >= current_version)
        return UserOnboardingState(completed=completed, completed_at=str(completed_at) if completed else None, version=version)

    def update_user_onboarding_state(self, user_id: str, *, completed: bool, version: int) -> UserOnboardingState:
        now = _iso(_utc_now())
        completed_at = now if completed else None
        normalized_version = max(0, int(version))
        with self._connect() as conn:
            conn.execute(
                """
                insert into user_settings
                  (user_id, settings_json, updated_at, onboarding_completed_at, onboarding_version)
                values (?, '{}', ?, ?, ?)
                on conflict(user_id) do update set
                  onboarding_completed_at = excluded.onboarding_completed_at,
                  onboarding_version = excluded.onboarding_version,
                  updated_at = excluded.updated_at
                """,
                (user_id, now, completed_at, normalized_version),
            )
            conn.commit()
        return self.get_user_onboarding_state(user_id, current_version=normalized_version)

    def get_user_settings(self, user_id: str) -> UserSettings:
        with self._connect() as conn:
            row = conn.execute("select * from user_settings where user_id = ?", (user_id,)).fetchone()
        if row is None:
            return UserSettings()
        runtime = RuntimePreferenceSettings.model_validate_json(str(row["settings_json"] or "{}"))
        git_token = self.secret_box.decrypt_optional(row["git_token_encrypted"])
        slack_bot_token = self.secret_box.decrypt_optional(row["slack_bot_token_encrypted"])
        slack_app_token = self.secret_box.decrypt_optional(row["slack_app_token_encrypted"])
        return UserSettings(
            runtime=runtime,
            git_token_username=row["git_token_username"],
            git_token_configured=git_token is not None,
            git_token_masked=mask_secret(git_token),
            slack_bot_token_configured=slack_bot_token is not None,
            slack_bot_token_masked=mask_secret(slack_bot_token),
            slack_app_token_configured=slack_app_token is not None,
            slack_app_token_masked=mask_secret(slack_app_token),
        )

    def update_user_settings(
        self,
        user_id: str,
        runtime: RuntimePreferenceSettings,
        *,
        secrets_update: UserSecretUpdate | None = None,
    ) -> UserSettings:
        existing = self._raw_user_settings(user_id)
        secrets_update = secrets_update or UserSecretUpdate()
        git_token_encrypted = existing.get("git_token_encrypted")
        slack_bot_token_encrypted = existing.get("slack_bot_token_encrypted")
        slack_app_token_encrypted = existing.get("slack_app_token_encrypted")
        git_token_username = secrets_update.git_token_username if secrets_update.git_token_username is not None else existing.get("git_token_username")
        if secrets_update.git_token is not None:
            git_token_encrypted = self.secret_box.encrypt_optional(secrets_update.git_token.strip() or None)
        if secrets_update.slack_bot_token is not None:
            slack_bot_token_encrypted = self.secret_box.encrypt_optional(secrets_update.slack_bot_token.strip() or None)
        if secrets_update.slack_app_token is not None:
            slack_app_token_encrypted = self.secret_box.encrypt_optional(secrets_update.slack_app_token.strip() or None)
        now = _iso(_utc_now())
        payload = runtime.model_dump_json(exclude_none=True)
        with self._connect() as conn:
            conn.execute(
                """
                insert into user_settings
                  (user_id, settings_json, git_token_encrypted, git_token_username, slack_bot_token_encrypted, slack_app_token_encrypted, updated_at)
                values (?, ?, ?, ?, ?, ?, ?)
                on conflict(user_id) do update set
                  settings_json = excluded.settings_json,
                  git_token_encrypted = excluded.git_token_encrypted,
                  git_token_username = excluded.git_token_username,
                  slack_bot_token_encrypted = excluded.slack_bot_token_encrypted,
                  slack_app_token_encrypted = excluded.slack_app_token_encrypted,
                  updated_at = excluded.updated_at
                """,
                (user_id, payload, git_token_encrypted, git_token_username, slack_bot_token_encrypted, slack_app_token_encrypted, now),
            )
            conn.commit()
        return self.get_user_settings(user_id)

    def git_credentials_for_user(self, user_id: str | None) -> tuple[str | None, str | None]:
        if not user_id:
            return None, None
        raw = self._raw_user_settings(user_id)
        token = self.secret_box.decrypt_optional(raw.get("git_token_encrypted"))
        username = (raw.get("git_token_username") or "x-access-token").strip() or "x-access-token"
        return token, username

    def slack_credentials_for_user(self, user_id: str | None) -> tuple[str | None, str | None]:
        if not user_id:
            return None, None
        raw = self._raw_user_settings(user_id)
        return (
            self.secret_box.decrypt_optional(raw.get("slack_bot_token_encrypted")),
            self.secret_box.decrypt_optional(raw.get("slack_app_token_encrypted")),
        )

    def slack_credentials_for_project(self, repo_root: str | None) -> tuple[str | None, str | None]:
        if not repo_root:
            return None, None
        raw = self._raw_project_settings(repo_root)
        return (
            self.secret_box.decrypt_optional(raw.get("slack_bot_token_encrypted")),
            self.secret_box.decrypt_optional(raw.get("slack_app_token_encrypted")),
        )

    def get_project_settings(self, repo_root: str) -> ProjectSettings:
        normalized = str(Path(repo_root).expanduser().resolve())
        with self._connect() as conn:
            row = conn.execute("select * from project_settings where repo_root = ?", (normalized,)).fetchone()
        if row is None:
            return ProjectSettings(repo_root=normalized)
        data = json.loads(str(row["settings_json"] or "{}"))
        data["repo_root"] = normalized
        slack_bot_token = self.secret_box.decrypt_optional(row["slack_bot_token_encrypted"])
        slack_app_token = self.secret_box.decrypt_optional(row["slack_app_token_encrypted"])
        data.update(
            {
                "slack_bot_token_configured": slack_bot_token is not None,
                "slack_bot_token_masked": mask_secret(slack_bot_token),
                "slack_app_token_configured": slack_app_token is not None,
                "slack_app_token_masked": mask_secret(slack_app_token),
            }
        )
        return ProjectSettings.model_validate(data)

    def update_project_settings(self, settings: ProjectSettings) -> ProjectSettings:
        normalized = str(Path(settings.repo_root).expanduser().resolve())
        stored = settings.model_copy(update={"repo_root": normalized})
        existing = self._raw_project_settings(normalized)
        slack_bot_token_encrypted = existing.get("slack_bot_token_encrypted")
        slack_app_token_encrypted = existing.get("slack_app_token_encrypted")
        if "slack_bot_token" in settings.model_fields_set:
            slack_bot_token_encrypted = self.secret_box.encrypt_optional(_secret_value(settings.slack_bot_token))
        if "slack_app_token" in settings.model_fields_set:
            slack_app_token_encrypted = self.secret_box.encrypt_optional(_secret_value(settings.slack_app_token))
        now = _iso(_utc_now())
        payload = stored.model_dump_json(
            exclude_none=True,
            exclude={
                "slack_bot_token",
                "slack_bot_token_configured",
                "slack_bot_token_masked",
                "slack_app_token",
                "slack_app_token_configured",
                "slack_app_token_masked",
            },
        )
        with self._connect() as conn:
            conn.execute(
                """
                insert into project_settings (repo_root, settings_json, slack_bot_token_encrypted, slack_app_token_encrypted, updated_at)
                values (?, ?, ?, ?, ?)
                on conflict(repo_root) do update set
                  settings_json = excluded.settings_json,
                  slack_bot_token_encrypted = excluded.slack_bot_token_encrypted,
                  slack_app_token_encrypted = excluded.slack_app_token_encrypted,
                  updated_at = excluded.updated_at
                """,
                (normalized, payload, slack_bot_token_encrypted, slack_app_token_encrypted, now),
            )
            conn.commit()
        return self.get_project_settings(normalized)

    def _raw_user_settings(self, user_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("select * from user_settings where user_id = ?", (user_id,)).fetchone()
        return dict(row) if row is not None else {}

    def _raw_project_settings(self, repo_root: str) -> dict[str, Any]:
        normalized = str(Path(repo_root).expanduser().resolve())
        with self._connect() as conn:
            row = conn.execute("select * from project_settings where repo_root = ?", (normalized,)).fetchone()
        return dict(row) if row is not None else {}

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists users (
                  user_id text primary key,
                  username text not null unique,
                  password_hash text not null,
                  is_admin integer not null default 0,
                  created_at text not null,
                  updated_at text not null
                );
                create table if not exists sessions (
                  token_hash text primary key,
                  user_id text not null references users(user_id) on delete cascade,
                  created_at text not null,
                  expires_at text not null
                );
                create table if not exists user_settings (
                  user_id text primary key references users(user_id) on delete cascade,
                  settings_json text not null default '{}',
                  git_token_encrypted text,
                  git_token_username text,
                  slack_bot_token_encrypted text,
                  slack_app_token_encrypted text,
                  onboarding_completed_at text,
                  onboarding_version integer not null default 0,
                  updated_at text not null
                );
                create table if not exists project_settings (
                  repo_root text primary key,
                  settings_json text not null default '{}',
                  slack_bot_token_encrypted text,
                  slack_app_token_encrypted text,
                  updated_at text not null
                );
                """
            )
            existing_columns = {row["name"] for row in conn.execute("pragma table_info(project_settings)").fetchall()}
            if "slack_bot_token_encrypted" not in existing_columns:
                conn.execute("alter table project_settings add column slack_bot_token_encrypted text")
            if "slack_app_token_encrypted" not in existing_columns:
                conn.execute("alter table project_settings add column slack_app_token_encrypted text")
            existing_user_settings_columns = {row["name"] for row in conn.execute("pragma table_info(user_settings)").fetchall()}
            if "onboarding_completed_at" not in existing_user_settings_columns:
                conn.execute("alter table user_settings add column onboarding_completed_at text")
            if "onboarding_version" not in existing_user_settings_columns:
                conn.execute("alter table user_settings add column onboarding_version integer not null default 0")
            conn.commit()

    def _normalize_username(self, username: str) -> str:
        normalized = username.strip().lower()
        if not normalized:
            raise ValueError("username is required")
        if len(normalized) > 80:
            raise ValueError("username is too long")
        return normalized
