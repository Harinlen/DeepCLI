"""Tests for HookRegistry."""

from __future__ import annotations

from daemon.extensions.hooks.base import HookConfig, HookEvent, HookType
from daemon.extensions.hooks.registry import HookRegistry


def _make_hook(
    event: HookEvent = HookEvent.PRE_TOOL_USE,
    hook_type: HookType = HookType.COMMAND,
    if_: str | None = None,
    command: str | None = "echo ok",
) -> HookConfig:
    """Helper to build a HookConfig."""
    return HookConfig(event=event, type=hook_type, if_=if_, command=command)


class TestHookRegistry:
    """Tests for HookRegistry."""

    def test_register_and_count(self) -> None:
        """Register hooks and check count."""
        reg = HookRegistry()
        reg.register(_make_hook())
        reg.register(_make_hook(event=HookEvent.STOP))
        assert reg.hook_count == 2

    def test_get_hooks_by_event(self) -> None:
        """get_hooks returns only hooks for the given event."""
        reg = HookRegistry()
        pre = _make_hook(event=HookEvent.PRE_TOOL_USE)
        post = _make_hook(event=HookEvent.POST_TOOL_USE)
        reg.register(pre)
        reg.register(post)

        result = reg.get_hooks(HookEvent.PRE_TOOL_USE, "bash", {})
        assert result == [pre]

    def test_get_hooks_empty(self) -> None:
        """get_hooks returns empty list for unregistered event."""
        reg = HookRegistry()
        assert reg.get_hooks(HookEvent.STOP) == []

    def test_if_condition_filters(self) -> None:
        """Hook with if_ condition only matches matching tools."""
        reg = HookRegistry()
        hook = _make_hook(if_="Bash(rm *)")
        reg.register(hook)

        # Matches
        result = reg.get_hooks(HookEvent.PRE_TOOL_USE, "bash", {"command": "rm -rf /"})
        assert result == [hook]

        # Doesn't match — different command
        result = reg.get_hooks(HookEvent.PRE_TOOL_USE, "bash", {"command": "ls -la"})
        assert result == []

        # Doesn't match — different tool
        result = reg.get_hooks(HookEvent.PRE_TOOL_USE, "file_read", {"path": "rm.txt"})
        assert result == []

    def test_no_if_matches_all(self) -> None:
        """Hook without if_ matches all tool calls for that event."""
        reg = HookRegistry()
        hook = _make_hook(if_=None)
        reg.register(hook)

        result = reg.get_hooks(HookEvent.PRE_TOOL_USE, "bash", {"command": "anything"})
        assert result == [hook]

        result = reg.get_hooks(HookEvent.PRE_TOOL_USE, "file_read", {"path": "/etc"})
        assert result == [hook]

    def test_stop_event_returns_all(self) -> None:
        """Stop event ignores if_ conditions and returns all hooks."""
        reg = HookRegistry()
        hook = _make_hook(event=HookEvent.STOP, if_="Bash(rm *)")
        reg.register(hook)

        # Returns the hook even without tool context
        result = reg.get_hooks(HookEvent.STOP)
        assert result == [hook]

    def test_ordering_preserved(self) -> None:
        """Hooks are returned in registration order."""
        reg = HookRegistry()
        h1 = _make_hook(command="echo first")
        h2 = _make_hook(command="echo second")
        reg.register(h1)
        reg.register(h2)

        result = reg.get_hooks(HookEvent.PRE_TOOL_USE, "bash", {"command": "rm x"})
        assert result == [h1, h2]

    def test_invalid_if_skipped(self) -> None:
        """Hook with invalid if_ is not registered."""
        reg = HookRegistry()
        hook = _make_hook(if_="123invalid!!!")
        reg.register(hook)
        assert reg.hook_count == 0

    def test_clear(self) -> None:
        """clear() removes all hooks."""
        reg = HookRegistry()
        reg.register(_make_hook())
        reg.register(_make_hook(event=HookEvent.STOP))
        reg.clear()
        assert reg.hook_count == 0

    def test_if_with_no_tool_context(self) -> None:
        """Hook with if_ condition skipped when no tool_name given."""
        reg = HookRegistry()
        hook = _make_hook(if_="Bash(rm *)")
        reg.register(hook)

        result = reg.get_hooks(HookEvent.PRE_TOOL_USE)
        assert result == []
