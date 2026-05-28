from __future__ import annotations

from .antigravity_adapter import SubprocessAntigravityAdapter
from .assistant_adapter import AssistantAdapter
from .claude_adapter import SubprocessClaudeAdapter
from .codex_adapter import SubprocessCodexAdapter
from .config import AppConfig, AssistantBackend
from .gemini_adapter import SubprocessGeminiAdapter
from .opencode_adapter import SubprocessOpenCodeAdapter


def build_adapter(backend: AssistantBackend) -> AssistantAdapter:
    if backend == "claude":
        return SubprocessClaudeAdapter()
    if backend == "codex":
        return SubprocessCodexAdapter()
    if backend == "antigravity":
        return SubprocessAntigravityAdapter()
    if backend == "gemini":
        return SubprocessGeminiAdapter()
    if backend == "opencode":
        return SubprocessOpenCodeAdapter()
    raise NotImplementedError(f"unsupported coding assistant: {backend}")


def build_adapter_registry() -> dict[AssistantBackend, AssistantAdapter]:
    return {
        "claude": build_adapter("claude"),
        "codex": build_adapter("codex"),
        "antigravity": build_adapter("antigravity"),
        "gemini": build_adapter("gemini"),
        "opencode": build_adapter("opencode"),
    }


def build_role_adapters(config: AppConfig, *, adapter_registry: dict[AssistantBackend, AssistantAdapter] | None = None):
    registry = adapter_registry or build_adapter_registry()
    planner_adapter = registry[config.backend_for_role("planner")]
    implementer_adapter = registry[config.backend_for_role("implementer")]
    reviewer_adapter = registry[config.backend_for_role("reviewer")]
    commit_adapter = registry[config.backend_for_role("commit")]
    branch_summary_adapter = registry[config.backend_for_role("planner")]
    return planner_adapter, implementer_adapter, reviewer_adapter, commit_adapter, branch_summary_adapter
