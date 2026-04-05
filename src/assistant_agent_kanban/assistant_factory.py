from __future__ import annotations

from .assistant_adapter import AssistantAdapter
from .codex_adapter import SubprocessCodexAdapter
from .config import AppConfig, AssistantBackend
from .gemini_adapter import SubprocessGeminiAdapter
from .opencode_adapter import SubprocessOpenCodeAdapter


def build_adapter(backend: AssistantBackend) -> AssistantAdapter:
    if backend == "opencode":
        return SubprocessOpenCodeAdapter()
    if backend == "codex":
        return SubprocessCodexAdapter()
    if backend == "gemini":
        return SubprocessGeminiAdapter()
    raise NotImplementedError(f"unsupported coding assistant: {backend}")


def build_adapter_registry() -> dict[AssistantBackend, AssistantAdapter]:
    return {
        "opencode": build_adapter("opencode"),
        "codex": build_adapter("codex"),
        "gemini": build_adapter("gemini"),
    }


def build_role_adapters(config: AppConfig, *, adapter_registry: dict[AssistantBackend, AssistantAdapter] | None = None):
    registry = adapter_registry or build_adapter_registry()
    planner_adapter = registry[config.backend_for_role("planner")]
    implementer_adapter = registry[config.backend_for_role("implementer")]
    reviewer_adapter = registry[config.backend_for_role("reviewer")]
    commit_adapter = registry[config.backend_for_role("commit")]
    branch_summary_adapter = registry[config.backend_for_role("planner")]
    return planner_adapter, implementer_adapter, reviewer_adapter, commit_adapter, branch_summary_adapter
