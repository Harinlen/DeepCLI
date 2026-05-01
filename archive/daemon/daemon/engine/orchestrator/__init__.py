"""Orchestrator package — re-exports :class:`Orchestrator`.

The orchestrator uses a composition architecture: the main class
delegates to independent subsystems (ToolExecutor, Compactor,
MemoryManager, etc.) rather than inheriting from mixins.

Public API:
    - ``Orchestrator`` — the main class
    - ``PermissionCallback`` — type alias for permission prompts
"""

from __future__ import annotations

from daemon.engine.orchestrator.orchestrator import Orchestrator, PermissionCallback

__all__ = ["Orchestrator", "PermissionCallback"]
