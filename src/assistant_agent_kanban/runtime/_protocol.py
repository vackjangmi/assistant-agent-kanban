"""Type-only protocol used by the Slack handler mixin.

``_SlackHandlersMixin`` calls a wide variety of attributes/methods on
``self`` that are actually provided by ``RuntimeSupervisor``: services
wired in ``__init__`` (``self.task_service``, ``self.scanner``,
``self.verification_service`` …), runtime collaborators
(``self.slack_runtime``, ``self.events``), and internal helpers. Rather
than enumerate them all here, this protocol exposes a permissive
``__getattr__`` returning ``Any`` so pyright is satisfied without us
restating the supervisor's full surface.

At runtime the mixin only inherits from ``object``; the protocol is
imported under ``TYPE_CHECKING`` only.
"""
from __future__ import annotations

from typing import Any


class _RuntimeSupervisorLike:
    """Loose static stub for the Slack mixin's view of ``RuntimeSupervisor``.

    Only consulted by pyright (the mixin inherits this under
    ``TYPE_CHECKING`` only). The ``__getattr__`` lets the mixin reach
    any attribute or method that the real ``RuntimeSupervisor`` exposes
    without us having to enumerate the supervisor's entire surface.
    """

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)
