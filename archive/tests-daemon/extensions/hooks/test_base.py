"""Tests for hook base models and enums."""

from __future__ import annotations

from daemon.extensions.hooks.base import (
    HookConfig,
    HookContext,
    HookEvent,
    HookResult,
    HookType,
)


class TestHookEvent:
    """Tests for HookEvent enum."""

    def test_values(self) -> None:
        """All P0 events are defined."""
        assert HookEvent.PRE_TOOL_USE.value == "pre_tool_use"
        assert HookEvent.POST_TOOL_USE.value == "post_tool_use"
        assert HookEvent.STOP.value == "stop"

    def test_from_string(self) -> None:
        """Can construct from string value."""
        assert HookEvent("pre_tool_use") == HookEvent.PRE_TOOL_USE


class TestHookType:
    """Tests for HookType enum."""

    def test_values(self) -> None:
        """All Phase 3 types are defined."""
        assert HookType.COMMAND.value == "command"
        assert HookType.PROMPT.value == "prompt"
        assert HookType.HTTP.value == "http"


class TestHookConfig:
    """Tests for HookConfig dataclass."""

    def test_minimal(self) -> None:
        """Minimal config with just event and type."""
        cfg = HookConfig(event=HookEvent.STOP, type=HookType.COMMAND)
        assert cfg.event == HookEvent.STOP
        assert cfg.type == HookType.COMMAND
        assert cfg.if_ is None
        assert cfg.timeout == 30
        assert cfg.async_ is False

    def test_command_hook(self) -> None:
        """Command hook with all fields."""
        cfg = HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            type=HookType.COMMAND,
            if_="Bash(rm *)",
            command="echo blocked && exit 1",
            timeout=10,
            async_=False,
        )
        assert cfg.command == "echo blocked && exit 1"
        assert cfg.if_ == "Bash(rm *)"

    def test_http_hook(self) -> None:
        """HTTP hook with headers."""
        cfg = HookConfig(
            event=HookEvent.POST_TOOL_USE,
            type=HookType.HTTP,
            url="https://example.com/webhook",
            headers={"Authorization": "Bearer $TOKEN"},
        )
        assert cfg.url == "https://example.com/webhook"
        assert cfg.headers["Authorization"] == "Bearer $TOKEN"

    def test_frozen(self) -> None:
        """HookConfig is immutable."""
        cfg = HookConfig(event=HookEvent.STOP, type=HookType.COMMAND)
        try:
            cfg.timeout = 99  # type: ignore[misc]
            raise AssertionError("Should be frozen")
        except AttributeError:
            pass


class TestHookContext:
    """Tests for HookContext dataclass."""

    def test_defaults(self) -> None:
        """Default context has no tool info."""
        ctx = HookContext()
        assert ctx.tool_name is None
        assert ctx.tool_input == {}
        assert ctx.tool_output is None

    def test_with_tool_info(self) -> None:
        """Context with full tool info."""
        ctx = HookContext(
            tool_name="bash",
            tool_input={"command": "ls"},
            tool_output="file.txt",
        )
        assert ctx.tool_name == "bash"
        assert ctx.tool_input == {"command": "ls"}
        assert ctx.tool_output == "file.txt"


class TestHookResult:
    """Tests for HookResult dataclass."""

    def test_defaults(self) -> None:
        """Default result is non-blocking."""
        result = HookResult()
        assert result.blocked is False
        assert result.output is None

    def test_blocked(self) -> None:
        """Blocked result with output."""
        result = HookResult(blocked=True, output="BLOCKED: rm command")
        assert result.blocked is True
        assert result.output == "BLOCKED: rm command"
