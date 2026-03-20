from __future__ import annotations

from .assistant_adapter import AssistantAdapter
from .codex_adapter import SubprocessCodexAdapter
from .config import AppConfig, AssistantBackend
from .opencode_adapter import SubprocessOpenCodeAdapter


def build_adapter(backend: AssistantBackend) -> AssistantAdapter:
    if backend == "opencode":
        return SubprocessOpenCodeAdapter()
    if backend == "codex":
        return SubprocessCodexAdapter()
    raise NotImplementedError(f"unsupported coding assistant: {backend}")


def build_adapter_registry() -> dict[AssistantBackend, AssistantAdapter]:
    return {
        "opencode": build_adapter("opencode"),
        "codex": build_adapter("codex"),
    }


def build_role_adapters(config: AppConfig, *, adapter_registry: dict[AssistantBackend, AssistantAdapter] | None = None):
    registry = adapter_registry or build_adapter_registry()
    adapter = registry[config.runtime.coding_assistant]
    return adapter, adapter, adapter, adapter, adapter
