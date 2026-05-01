"""Tests for Phase 5.5.4E — Hook event expansion.

Covers:
- New HookEvent enum values
- Extended HookContext fields
- Extended HookResult fields (modified_input, permission)
- JSON structured output parsing in command hooks
- Registry behavior with new non-tool events
- Runner propagation of modified_input/permission
"""

from __future__ import annotations

import json

import pytest

from daemon.extensions.hooks.base import (
    HookConfig,
    HookContext,
    HookEvent,
    HookResult,
    HookType,
)
from daemon.extensions.hooks.registry import HookRegistry
from daemon.extensions.hooks.runner import (
    _try_parse_json_result,
    run_hooks,
)


# -- HookEvent enum ---------------------------------------------------------


class TestHookEventExpansion:
    """Verify all 12 hook events exist and have correct string values."""

    @pytest.mark.parametrize(
        "event,value",
        [
            (HookEvent.PRE_TOOL_USE, "pre_tool_use"),
            (HookEvent.POST_TOOL_USE, "post_tool_use"),
            (HookEvent.POST_TOOL_FAILURE, "post_tool_failure"),
            (HookEvent.SESSION_START, "session_start"),
            (HookEvent.SESSION_END, "session_end"),
            (HookEvent.STOP, "stop"),
            (HookEvent.USER_PROMPT_SUBMIT, "user_prompt_submit"),
            (HookEvent.PRE_COMPACT, "pre_compact"),
            (HookEvent.POST_COMPACT, "post_compact"),
            (HookEvent.FILE_CHANGED, "file_changed"),
            (HookEvent.SUBAGENT_START, "subagent_start"),
            (HookEvent.PERMISSION_DENIED, "permission_denied"),
        ],
    )
    def test_event_values(self, event: HookEvent, value: str) -> None:
        assert event.value == value

    def test_total_event_count(self) -> None:
        assert len(HookEvent) == 12


# -- HookContext expanded fields --------------------------------------------


class TestHookContextExpansion:
    """Verify new optional fields on HookContext."""

    def test_default_values(self) -> None:
        ctx = HookContext()
        assert ctx.tool_name is None
        assert ctx.error_message is None
        assert ctx.session_id is None
        assert ctx.user_text is None
        assert ctx.file_path is None
        assert ctx.agent_description is None
        assert ctx.depth is None

    def test_session_context(self) -> None:
        ctx = HookContext(session_id="abc123", cwd="/tmp", is_resume=True)
        assert ctx.session_id == "abc123"
        assert ctx.cwd == "/tmp"
        assert ctx.is_resume is True

    def test_file_context(self) -> None:
        ctx = HookContext(file_path="/foo/bar.py", change_type="edit")
        assert ctx.file_path == "/foo/bar.py"
        assert ctx.change_type == "edit"

    def test_compaction_context(self) -> None:
        ctx = HookContext(message_count=20, token_estimate=5000)
        assert ctx.message_count == 20
        assert ctx.token_estimate == 5000

    def test_agent_context(self) -> None:
        ctx = HookContext(agent_description="search code", depth=2)
        assert ctx.agent_description == "search code"
        assert ctx.depth == 2

    def test_error_context(self) -> None:
        ctx = HookContext(tool_name="bash", error_message="timeout")
        assert ctx.tool_name == "bash"
        assert ctx.error_message == "timeout"


# -- HookResult expanded fields ---------------------------------------------


class TestHookResultExpansion:
    """Verify new fields on HookResult."""

    def test_default_values(self) -> None:
        r = HookResult()
        assert r.blocked is False
        assert r.output is None
        assert r.modified_input is None
        assert r.permission is None

    def test_modified_input(self) -> None:
        r = HookResult(modified_input={"user_text": "rewritten"})
        assert r.modified_input == {"user_text": "rewritten"}

    def test_permission(self) -> None:
        r = HookResult(permission="allow")
        assert r.permission == "allow"


# -- JSON structured output parsing -----------------------------------------


class TestTryParseJsonResult:
    """Test _try_parse_json_result helper."""

    def test_none_input(self) -> None:
        assert _try_parse_json_result(None) is None

    def test_empty_input(self) -> None:
        assert _try_parse_json_result(b"") is None

    def test_non_json(self) -> None:
        assert _try_parse_json_result(b"just text output") is None

    def test_json_no_recognized_keys(self) -> None:
        assert _try_parse_json_result(b'{"foo": "bar"}') is None

    def test_json_with_blocked(self) -> None:
        data = json.dumps({"blocked": True}).encode()
        result = _try_parse_json_result(data)
        assert result is not None
        assert result["blocked"] is True

    def test_json_with_modified_input(self) -> None:
        data = json.dumps({"modified_input": {"user_text": "hello"}}).encode()
        result = _try_parse_json_result(data)
        assert result is not None
        assert result["modified_input"]["user_text"] == "hello"

    def test_json_with_permission(self) -> None:
        data = json.dumps({"permission": "deny"}).encode()
        result = _try_parse_json_result(data)
        assert result is not None
        assert result["permission"] == "deny"


# -- Registry with new events -----------------------------------------------


def _make_hook(
    event: HookEvent = HookEvent.PRE_TOOL_USE,
    if_: str | None = None,
) -> HookConfig:
    return HookConfig(event=event, type=HookType.COMMAND, command="echo ok", if_=if_)


class TestRegistryNewEvents:
    """Registry behavior with new non-tool events."""

    @pytest.mark.parametrize(
        "event",
        [
            HookEvent.SESSION_START,
            HookEvent.SESSION_END,
            HookEvent.USER_PROMPT_SUBMIT,
            HookEvent.PRE_COMPACT,
            HookEvent.POST_COMPACT,
            HookEvent.FILE_CHANGED,
            HookEvent.SUBAGENT_START,
        ],
    )
    def test_non_tool_events_return_all(self, event: HookEvent) -> None:
        """Non-tool events return all registered hooks regardless of if_."""
        reg = HookRegistry()
        h1 = HookConfig(event=event, type=HookType.COMMAND, command="echo a")
        h2 = HookConfig(event=event, type=HookType.COMMAND, command="echo b", if_="Bash(*)")
        reg.register(h1)
        reg.register(h2)
        result = reg.get_hooks(event)
        assert len(result) == 2

    def test_tool_events_still_filter(self) -> None:
        """pre_tool_use still filters by tool_name when if_ is set."""
        reg = HookRegistry()
        hook = _make_hook(if_="Bash(rm *)")
        reg.register(hook)
        # No tool context → should skip
        assert reg.get_hooks(HookEvent.PRE_TOOL_USE) == []
        # With matching tool context → should match
        assert len(reg.get_hooks(HookEvent.PRE_TOOL_USE, "Bash", {"command": "rm -rf /"})) == 1

    def test_permission_denied_uses_tool_filter(self) -> None:
        """permission_denied is a tool event (has tool_name), so if_ filtering applies."""
        reg = HookRegistry()
        hook = HookConfig(
            event=HookEvent.PERMISSION_DENIED,
            type=HookType.COMMAND,
            command="echo denied",
            if_="Bash(*)",
        )
        reg.register(hook)
        # With matching tool
        result = reg.get_hooks(HookEvent.PERMISSION_DENIED, "Bash", {"command": "rm"})
        assert len(result) == 1
        # Without matching tool
        result = reg.get_hooks(HookEvent.PERMISSION_DENIED, "file_read", {})
        assert len(result) == 0

    def test_post_tool_failure_uses_tool_filter(self) -> None:
        """post_tool_failure is a tool event, if_ filtering applies."""
        reg = HookRegistry()
        hook = HookConfig(
            event=HookEvent.POST_TOOL_FAILURE,
            type=HookType.COMMAND,
            command="echo fail",
            if_="Bash(*)",
        )
        reg.register(hook)
        result = reg.get_hooks(HookEvent.POST_TOOL_FAILURE, "Bash", {"command": "bad"})
        assert len(result) == 1


# -- Runner propagation of modified_input/permission -------------------------


class TestRunnerPropagation:
    """run_hooks propagates modified_input and permission from last hook."""

    @pytest.mark.asyncio
    async def test_propagates_modified_input(self) -> None:
        """Structured JSON with modified_input propagates through run_hooks."""
        # Create a command hook that outputs JSON
        hook = HookConfig(
            event=HookEvent.USER_PROMPT_SUBMIT,
            type=HookType.COMMAND,
            command='echo \'{"modified_input": {"user_text": "rewritten"}}\'',
        )
        ctx = HookContext(user_text="original")
        result = await run_hooks([hook], ctx)
        assert result.blocked is False
        assert result.modified_input == {"user_text": "rewritten"}

    @pytest.mark.asyncio
    async def test_blocking_with_json(self) -> None:
        """Non-zero exit + JSON → blocked with modified_input."""
        hook = HookConfig(
            event=HookEvent.USER_PROMPT_SUBMIT,
            type=HookType.COMMAND,
            command='echo \'{"blocked": true, "output": "denied"}\' && exit 1',
        )
        ctx = HookContext(user_text="test")
        result = await run_hooks([hook], ctx)
        assert result.blocked is True


# -- FileChanged side-effect -------------------------------------------------


class TestFileChangedSideEffect:
    """Verify FileChanged side-effect on file tools."""

    def test_file_changed_import(self) -> None:
        from daemon.side_effects import FileChanged

        fc = FileChanged(file_path="/foo/bar.py", change_type="edit")
        assert fc.type == "file_changed"
        assert fc.file_path == "/foo/bar.py"

    def test_file_changed_in_side_effect_union(self) -> None:
        from daemon.extensions.tools.base import ToolResult
        from daemon.side_effects import FileChanged

        result = ToolResult(
            output="ok",
            side_effect=FileChanged(file_path="/foo.py", change_type="write"),
        )
        assert result.side_effect is not None
        assert result.side_effect.type == "file_changed"
