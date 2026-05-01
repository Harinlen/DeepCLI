"""Tests for hook runner executors."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.extensions.hooks.base import (
    HookConfig,
    HookContext,
    HookEvent,
    HookType,
)
from daemon.extensions.hooks.runner import (
    _build_hook_env,
    _interpolate_env,
    _run_command_hook,
    _run_http_hook,
    run_hook,
    run_hooks,
)


# ------------------------------------------------------------------
# _interpolate_env
# ------------------------------------------------------------------


class TestInterpolateEnv:
    """Tests for environment variable interpolation."""

    def test_simple_var(self) -> None:
        """Replace a simple $VAR."""
        with patch.dict("os.environ", {"TOKEN": "secret123"}):
            assert _interpolate_env("Bearer $TOKEN") == "Bearer secret123"

    def test_unknown_var_left_as_is(self) -> None:
        """Unknown variable is left unchanged."""
        with patch.dict("os.environ", {}, clear=True):
            assert _interpolate_env("$UNKNOWN_VAR") == "$UNKNOWN_VAR"

    def test_no_vars(self) -> None:
        """String without variables is returned unchanged."""
        assert _interpolate_env("plain text") == "plain text"

    def test_multiple_vars(self) -> None:
        """Multiple variables are replaced."""
        with patch.dict("os.environ", {"A": "1", "B": "2"}):
            assert _interpolate_env("$A and $B") == "1 and 2"

    def test_dollar_not_followed_by_alpha(self) -> None:
        """Dollar sign not followed by alpha is literal."""
        assert _interpolate_env("price: $5") == "price: $5"


# ------------------------------------------------------------------
# _build_hook_env
# ------------------------------------------------------------------


class TestBuildHookEnv:
    """Tests for hook environment variable construction."""

    def test_with_tool_info(self) -> None:
        """Env includes TOOL_NAME and TOOL_INPUT_JSON."""
        ctx = HookContext(tool_name="bash", tool_input={"command": "ls"})
        env = _build_hook_env(ctx)
        assert env["TOOL_NAME"] == "bash"
        assert json.loads(env["TOOL_INPUT_JSON"]) == {"command": "ls"}
        assert "TOOL_OUTPUT" not in env

    def test_with_tool_output(self) -> None:
        """Env includes TOOL_OUTPUT when present."""
        ctx = HookContext(tool_name="bash", tool_input={}, tool_output="result")
        env = _build_hook_env(ctx)
        assert env["TOOL_OUTPUT"] == "result"

    def test_without_tool_name(self) -> None:
        """Env omits TOOL_NAME when not set."""
        ctx = HookContext()
        env = _build_hook_env(ctx)
        assert "TOOL_NAME" not in env


# ------------------------------------------------------------------
# Command hook
# ------------------------------------------------------------------


class TestCommandHook:
    """Tests for command-type hook execution."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """Successful command hook returns non-blocking result."""
        hook = HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            type=HookType.COMMAND,
            command="echo hello",
        )
        ctx = HookContext(tool_name="bash", tool_input={"command": "ls"})
        result = await _run_command_hook(hook, ctx)
        assert not result.blocked
        assert result.output is not None
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_nonzero_exit_blocks_pre_tool(self) -> None:
        """Non-zero exit code on pre_tool_use blocks execution."""
        hook = HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            type=HookType.COMMAND,
            command="echo BLOCKED && exit 1",
        )
        ctx = HookContext(tool_name="bash", tool_input={"command": "rm -rf /"})
        result = await _run_command_hook(hook, ctx)
        assert result.blocked
        assert "BLOCKED" in (result.output or "")

    @pytest.mark.asyncio
    async def test_nonzero_exit_on_post_does_not_block(self) -> None:
        """Non-zero exit on post_tool_use does NOT block."""
        hook = HookConfig(
            event=HookEvent.POST_TOOL_USE,
            type=HookType.COMMAND,
            command="exit 1",
        )
        ctx = HookContext(tool_name="bash", tool_input={})
        result = await _run_command_hook(hook, ctx)
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        """Command that exceeds timeout returns non-blocking."""
        hook = HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            type=HookType.COMMAND,
            command="sleep 60",
            timeout=1,
        )
        ctx = HookContext(tool_name="bash", tool_input={})
        result = await _run_command_hook(hook, ctx)
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_no_command(self) -> None:
        """Hook with no command is skipped."""
        hook = HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            type=HookType.COMMAND,
            command=None,
        )
        ctx = HookContext()
        result = await _run_command_hook(hook, ctx)
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_env_vars_injected(self) -> None:
        """TOOL_NAME and TOOL_INPUT_JSON are available in the command."""
        hook = HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            type=HookType.COMMAND,
            command="echo $TOOL_NAME",
        )
        ctx = HookContext(tool_name="bash", tool_input={"command": "test"})
        result = await _run_command_hook(hook, ctx)
        assert result.output is not None
        assert "bash" in result.output


# ------------------------------------------------------------------
# HTTP hook
# ------------------------------------------------------------------


class TestHttpHook:
    """Tests for HTTP-type hook execution."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """Successful HTTP POST returns status code."""
        hook = HookConfig(
            event=HookEvent.POST_TOOL_USE,
            type=HookType.HTTP,
            url="https://example.com/webhook",
        )
        ctx = HookContext(tool_name="bash", tool_input={"command": "ls"})

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("daemon.extensions.hooks.runner.httpx.AsyncClient", return_value=mock_client):
            result = await _run_http_hook(hook, ctx)

        assert not result.blocked
        assert result.output == "HTTP 200"

    @pytest.mark.asyncio
    async def test_no_url(self) -> None:
        """Hook with no URL is skipped."""
        hook = HookConfig(
            event=HookEvent.POST_TOOL_USE,
            type=HookType.HTTP,
            url=None,
        )
        ctx = HookContext()
        result = await _run_http_hook(hook, ctx)
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_header_interpolation(self) -> None:
        """Headers with $ENV_VAR are interpolated."""
        hook = HookConfig(
            event=HookEvent.POST_TOOL_USE,
            type=HookType.HTTP,
            url="https://example.com",
            headers={"Authorization": "Bearer $MY_TOKEN"},
        )
        ctx = HookContext()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.dict("os.environ", {"MY_TOKEN": "abc123"}),
            patch("daemon.extensions.hooks.runner.httpx.AsyncClient", return_value=mock_client),
        ):
            await _run_http_hook(hook, ctx)

        # Verify the header was interpolated
        call_kwargs = mock_client.post.call_args
        assert "Bearer abc123" in call_kwargs.kwargs.get("headers", {}).get("Authorization", "")


# ------------------------------------------------------------------
# run_hook / run_hooks
# ------------------------------------------------------------------


class TestRunHook:
    """Tests for the run_hook dispatcher."""

    @pytest.mark.asyncio
    async def test_dispatches_command(self) -> None:
        """run_hook dispatches to command executor."""
        hook = HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            type=HookType.COMMAND,
            command="echo dispatched",
        )
        ctx = HookContext(tool_name="bash", tool_input={})
        result = await run_hook(hook, ctx)
        assert "dispatched" in (result.output or "")

    @pytest.mark.asyncio
    async def test_async_fires_background(self) -> None:
        """Async hook returns immediately (non-blocking)."""
        hook = HookConfig(
            event=HookEvent.POST_TOOL_USE,
            type=HookType.COMMAND,
            command="echo background",
            async_=True,
        )
        ctx = HookContext()
        result = await run_hook(hook, ctx)
        # Should return immediately, non-blocking
        assert not result.blocked
        # Give the background task time to complete
        await asyncio.sleep(0.1)


class TestRunHooks:
    """Tests for run_hooks (sequential execution)."""

    @pytest.mark.asyncio
    async def test_empty_list(self) -> None:
        """Empty list returns non-blocking."""
        result = await run_hooks([], HookContext())
        assert not result.blocked

    @pytest.mark.asyncio
    async def test_all_pass(self) -> None:
        """Multiple passing hooks combine output."""
        hooks = [
            HookConfig(
                event=HookEvent.PRE_TOOL_USE,
                type=HookType.COMMAND,
                command="echo first",
            ),
            HookConfig(
                event=HookEvent.PRE_TOOL_USE,
                type=HookType.COMMAND,
                command="echo second",
            ),
        ]
        ctx = HookContext(tool_name="bash", tool_input={})
        result = await run_hooks(hooks, ctx)
        assert not result.blocked
        assert "first" in (result.output or "")
        assert "second" in (result.output or "")

    @pytest.mark.asyncio
    async def test_early_block(self) -> None:
        """First blocking hook stops execution."""
        hooks = [
            HookConfig(
                event=HookEvent.PRE_TOOL_USE,
                type=HookType.COMMAND,
                command="echo BLOCKED && exit 1",
            ),
            HookConfig(
                event=HookEvent.PRE_TOOL_USE,
                type=HookType.COMMAND,
                command="echo should-not-run",
            ),
        ]
        ctx = HookContext(tool_name="bash", tool_input={})
        result = await run_hooks(hooks, ctx)
        assert result.blocked
        assert "BLOCKED" in (result.output or "")
        # Second hook should not have run
        assert "should-not-run" not in (result.output or "")
