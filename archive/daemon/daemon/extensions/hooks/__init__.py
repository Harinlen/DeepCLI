"""Hook system — event-driven extension points.

Hooks fire on events like ``pre_tool_use``, ``post_tool_use``, and
``stop``.  Each hook can be a shell command, an LLM prompt evaluation,
or an HTTP POST to an external URL.
"""

from daemon.extensions.hooks.base import HookConfig, HookContext, HookEvent, HookResult, HookType
from daemon.extensions.hooks.registry import HookRegistry
from daemon.extensions.hooks.runner import run_hook, run_hooks

__all__ = [
    "HookConfig",
    "HookContext",
    "HookEvent",
    "HookRegistry",
    "HookResult",
    "HookType",
    "run_hook",
    "run_hooks",
]
