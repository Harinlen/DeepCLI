"""Hook registry — stores hooks and retrieves matching ones by event.

Hooks are registered during startup from the resolved config.  At
runtime, the orchestrator queries the registry for hooks that match
a specific event and (optionally) a tool call.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from daemon.extensions.hooks.base import HookConfig, HookEvent
from daemon.permissions.rules import ToolRule, matches, parse_rule


def _hook_key(hook: HookConfig, index: int) -> str:
    """Build a stable cache key for a hook config.

    Uses the registration index to disambiguate hooks with identical
    event/type/if_ combinations.
    """
    return f"{hook.event.value}:{hook.type.value}:{hook.if_ or ''}:{index}"

logger = logging.getLogger(__name__)


class HookRegistry:
    """Registry of hooks indexed by event.

    Stores hooks and returns those matching a given event + optional
    tool call (filtered by the hook's ``if_`` condition).
    """

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[HookConfig]] = defaultdict(list)
        self._parsed_rules: dict[str, ToolRule | None] = {}
        self._hook_keys: dict[int, str] = {}  # id(hook) → stable key
        self._next_index = 0

    def register(self, hook: HookConfig) -> None:
        """Register a hook.

        If the hook has an ``if_`` condition, it is parsed eagerly so
        errors surface at startup rather than at runtime.

        Args:
            hook: The hook configuration to register.
        """
        # Eagerly parse the if_ condition
        if hook.if_ is not None:
            try:
                rule = parse_rule(hook.if_)
            except ValueError:
                logger.warning(
                    "Skipping hook with invalid 'if' condition: %r",
                    hook.if_,
                )
                return
            key = _hook_key(hook, self._next_index)
            self._parsed_rules[key] = rule
            self._hook_keys[id(hook)] = key
        else:
            key = _hook_key(hook, self._next_index)
            self._parsed_rules[key] = None
            self._hook_keys[id(hook)] = key
        self._next_index += 1

        self._hooks[hook.event].append(hook)
        logger.debug(
            "Registered %s hook (type=%s, if=%s)",
            hook.event.value,
            hook.type.value,
            hook.if_ or "*",
        )

    def get_hooks(
        self,
        event: HookEvent,
        tool_name: str | None = None,
        tool_input: dict[str, Any] | None = None,
    ) -> list[HookConfig]:
        """Return hooks matching an event and optional tool call.

        For tool-related events (``pre_tool_use``, ``post_tool_use``),
        hooks with ``if_`` conditions are filtered against the tool
        name and input.  Hooks without ``if_`` conditions always match.

        For non-tool events (``stop``), all hooks for that event are
        returned regardless of ``if_`` conditions.

        Args:
            event: The event type.
            tool_name: Tool name (for tool events).
            tool_input: Tool arguments dict (for tool events).

        Returns:
            List of matching hooks in registration order.
        """
        candidates = self._hooks.get(event, [])
        if not candidates:
            return []

        # Events that are never associated with a specific tool call.
        # Return all hooks unconditionally (if_ conditions are meaningless).
        _NON_TOOL_EVENTS = {
            HookEvent.STOP,
            HookEvent.SESSION_START,
            HookEvent.SESSION_END,
            HookEvent.USER_PROMPT_SUBMIT,
            HookEvent.PRE_COMPACT,
            HookEvent.POST_COMPACT,
            HookEvent.FILE_CHANGED,
            HookEvent.SUBAGENT_START,
        }
        if event in _NON_TOOL_EVENTS:
            return list(candidates)

        result: list[HookConfig] = []
        for hook in candidates:
            key = self._hook_keys.get(id(hook))
            rule = self._parsed_rules.get(key) if key is not None else None

            # No condition → always matches
            if rule is None:
                result.append(hook)
                continue

            # Has condition but no tool context → skip
            if tool_name is None:
                continue

            if matches(rule, tool_name, tool_input or {}):
                result.append(hook)

        return result

    @property
    def hook_count(self) -> int:
        """Total number of registered hooks across all events."""
        return sum(len(hooks) for hooks in self._hooks.values())

    def clear(self) -> None:
        """Remove all registered hooks."""
        self._hooks.clear()
        self._parsed_rules.clear()
