from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class OMODelegationTarget:
    key: str
    source_type: str
    model: str | None
    variant: str | None


@dataclass(slots=True)
class OMODelegationSnapshot:
    source_path: Path | None
    targets: list[OMODelegationTarget]
    error: str | None = None

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        if self.targets:
            return "ready"
        if self.source_path is not None:
            return "empty"
        return "missing"


def read_omo_delegation_snapshot() -> OMODelegationSnapshot:
    config_path = _resolve_omo_config_path()
    if config_path is None:
        return OMODelegationSnapshot(source_path=None, targets=[])
    try:
        payload = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return OMODelegationSnapshot(source_path=config_path, targets=[], error=str(exc))
    targets = [
        _target_from_mapping(payload, key="quick", source_type="category", mapping_name="categories"),
        _target_from_mapping(payload, key="explore", source_type="agent", mapping_name="agents"),
        _target_from_mapping(payload, key="librarian", source_type="agent", mapping_name="agents"),
    ]
    return OMODelegationSnapshot(
        source_path=config_path,
        targets=[target for target in targets if target is not None],
    )


def _resolve_omo_config_path() -> Path | None:
    candidate_roots: list[Path] = []
    xdg_root = os.environ.get("XDG_CONFIG_HOME")
    if xdg_root:
        candidate_roots.append(Path(xdg_root))
    candidate_roots.append(Path.home() / ".config")
    seen: set[Path] = set()
    for root in candidate_roots:
        resolved_root = root.expanduser().resolve()
        if resolved_root in seen:
            continue
        seen.add(resolved_root)
        for filename in ("oh-my-opencode.json", "oh-my-opencode.jsonc"):
            candidate = resolved_root / "opencode" / filename
            if candidate.exists():
                return candidate
    return None


def _target_from_mapping(payload: object, *, key: str, source_type: str, mapping_name: str) -> OMODelegationTarget | None:
    if not isinstance(payload, dict):
        return None
    mapping = payload.get(mapping_name)
    if not isinstance(mapping, dict):
        return None
    target = mapping.get(key)
    if not isinstance(target, dict):
        return None
    model = target.get("model")
    variant = target.get("variant")
    return OMODelegationTarget(
        key=key,
        source_type=source_type,
        model=model if isinstance(model, str) else None,
        variant=variant if isinstance(variant, str) else None,
    )
