from __future__ import annotations

from dataclasses import dataclass

from .config import SlackConfig
from .slack_api import slack_api_call, slack_error_message


@dataclass(slots=True)
class SlackSettingsTestCheck:
    name: str
    ok: bool
    message: str


@dataclass(slots=True)
class SlackSettingsTestResult:
    ok: bool
    summary: str
    checks: list[SlackSettingsTestCheck]
    uses_posted_values: bool = True
    receive_verification_mode: str = "readiness"

    def to_payload(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "checks": [
                {"name": check.name, "ok": check.ok, "message": check.message}
                for check in self.checks
            ],
            "uses_posted_values": self.uses_posted_values,
            "receive_verification_mode": self.receive_verification_mode,
        }


def run_slack_settings_test(slack: SlackConfig, *, uses_posted_values: bool = True) -> SlackSettingsTestResult:
    checks: list[SlackSettingsTestCheck] = []

    if not slack.enabled:
        checks.append(SlackSettingsTestCheck("enabled", False, "Slack integration is disabled."))
        return SlackSettingsTestResult(
            ok=False,
            summary="Enable Slack before running the test.",
            checks=checks,
            uses_posted_values=uses_posted_values,
        )

    checks.append(SlackSettingsTestCheck("enabled", True, "Slack integration is enabled."))

    if not slack.bot_token:
        checks.append(SlackSettingsTestCheck("bot_token", False, "Bot token is required for Slack testing."))
        return SlackSettingsTestResult(
            ok=False,
            summary="Add a bot token to verify Slack messaging.",
            checks=checks,
            uses_posted_values=uses_posted_values,
        )

    checks.append(SlackSettingsTestCheck("bot_token", True, "Bot token is configured."))

    if slack.socket_mode_enabled and slack.app_token:
        checks.append(SlackSettingsTestCheck("app_token", True, "App token is configured for Socket Mode."))
    elif slack.socket_mode_enabled:
        checks.append(SlackSettingsTestCheck("app_token", False, "App token is required to verify Socket Mode readiness."))
    else:
        checks.append(SlackSettingsTestCheck("app_token", True, "Socket Mode is disabled, so app token readiness is skipped."))

    auth_payload = slack_api_call("auth.test", token=slack.bot_token)
    if not auth_payload.get("ok"):
        error_message = slack_error_message(auth_payload, fallback="Slack auth.test failed.")
        checks.append(SlackSettingsTestCheck("auth_test", False, error_message))
        return SlackSettingsTestResult(
            ok=False,
            summary="Slack credentials were rejected by auth.test.",
            checks=checks,
            uses_posted_values=uses_posted_values,
        )

    workspace = auth_payload.get("team") or auth_payload.get("team_id") or "the Slack workspace"
    checks.append(SlackSettingsTestCheck("auth_test", True, f"Slack auth.test succeeded for {workspace}."))

    if not _is_plausible_channel(slack.default_channel):
        checks.append(
            SlackSettingsTestCheck(
                "send_test",
                False,
                "Set a default channel like #agent-alerts or a channel ID to verify message delivery.",
            )
        )
        return SlackSettingsTestResult(
            ok=False,
            summary="Slack auth works, but message delivery could not be verified without a default channel.",
            checks=checks,
            uses_posted_values=uses_posted_values,
        )

    post_payload = slack_api_call(
        "chat.postMessage",
        token=slack.bot_token,
        body={
            "channel": slack.default_channel,
            "text": "Assistant Agent Kanban Slack settings test: outbound messaging is working.",
        },
    )
    if not post_payload.get("ok"):
        error_message = slack_error_message(post_payload, fallback="Slack chat.postMessage failed.")
        checks.append(SlackSettingsTestCheck("send_test", False, error_message))
        return SlackSettingsTestResult(
            ok=False,
            summary="Slack auth succeeded, but the test message could not be delivered.",
            checks=checks,
            uses_posted_values=uses_posted_values,
        )

    checks.append(
        SlackSettingsTestCheck(
            "send_test",
            True,
            f"Posted a Slack test message to {slack.default_channel}.",
        )
    )

    if slack.socket_mode_enabled and slack.app_token:
        socket_payload = slack_api_call("apps.connections.open", token=slack.app_token)
        if not socket_payload.get("ok"):
            error_message = slack_error_message(socket_payload, fallback="Slack apps.connections.open failed.")
            checks.append(SlackSettingsTestCheck("receive_ready", False, error_message))
            return SlackSettingsTestResult(
                ok=False,
                summary="Message delivery worked, but Socket Mode readiness could not be verified.",
                checks=checks,
                uses_posted_values=uses_posted_values,
            )

        checks.append(
            SlackSettingsTestCheck(
                "receive_ready",
                True,
                "Socket Mode app token is valid and Slack issued a connection URL. Actual inbound events still require the running listener to observe real traffic.",
            )
        )
        return SlackSettingsTestResult(
            ok=True,
            summary="Slack test message was sent and Socket Mode readiness was verified. Actual inbound delivery remains a runtime readiness check until a real event is observed.",
            checks=checks,
            uses_posted_values=uses_posted_values,
        )

    if slack.socket_mode_enabled and not slack.app_token:
        checks.append(
            SlackSettingsTestCheck(
                "receive_ready",
                False,
                "Socket Mode readiness could not be verified because the app token is missing.",
            )
        )
        return SlackSettingsTestResult(
            ok=False,
            summary="Slack test message was sent, but Socket Mode readiness could not be verified without an app token.",
            checks=checks,
            uses_posted_values=uses_posted_values,
        )

    checks.append(
        SlackSettingsTestCheck(
            "receive_ready",
            True,
            "Socket Mode is disabled, so inbound readiness was not checked.",
        )
    )
    return SlackSettingsTestResult(
        ok=True,
        summary="Slack test message was sent successfully.",
        checks=checks,
        uses_posted_values=uses_posted_values,
    )


def _is_plausible_channel(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip()
    if not normalized:
        return False
    if normalized.startswith("#") and len(normalized) > 1:
        return True
    return normalized[:1] in {"C", "G", "D"} and len(normalized) >= 3
