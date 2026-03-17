from __future__ import annotations

from .codex_adapter import SubprocessCodexAdapter
from .config import AppConfig
from .opencode_adapter import SubprocessOpenCodeAdapter


def build_role_adapters(config: AppConfig):
    if config.runtime.coding_assistant == "opencode":
        adapter = SubprocessOpenCodeAdapter()
    elif config.runtime.coding_assistant == "codex":
        adapter = SubprocessCodexAdapter()
    else:
        raise NotImplementedError(f"unsupported coding assistant: {config.runtime.coding_assistant}")
    return adapter, adapter, adapter, adapter, adapter
