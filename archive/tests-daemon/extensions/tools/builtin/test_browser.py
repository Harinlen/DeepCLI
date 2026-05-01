"""Tests for BrowserTool — heavily mocked since CI has no Chrome."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from daemon.extensions.tools.base import PermissionLevel, ToolContext
from daemon.extensions.tools.builtin.browser import BrowserTool
from daemon.extensions.tools.builtin.subprocess_utils import SubprocessResult


@pytest.fixture
def tool() -> BrowserTool:
    return BrowserTool()


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


def _ok(stdout: str = "", stderr: str = "") -> SubprocessResult:
    return SubprocessResult(stdout=stdout, stderr=stderr, returncode=0)


def _fail(stderr: str = "boom", returncode: int = 1) -> SubprocessResult:
    return SubprocessResult(stdout="", stderr=stderr, returncode=returncode)


def _timeout() -> SubprocessResult:
    return SubprocessResult(stdout="", stderr="", returncode=-1, timed_out=True)


# ── Permission ────────────────────────────────────────────────


class TestPermission:
    def test_permission_level(self, tool: BrowserTool) -> None:
        assert tool.permission_level == PermissionLevel.PROMPT


# ── CLI availability ──────────────────────────────────────────


class TestCliAvailability:
    @pytest.mark.asyncio
    async def test_missing_cli_returns_actionable_error(
        self, tool: BrowserTool, ctx: ToolContext
    ) -> None:
        with patch(
            "daemon.extensions.tools.builtin.browser.agent_browser_cli.is_available",
            return_value=False,
        ):
            result = await tool.execute({"action": "page"}, ctx)
        assert result.is_error is True
        assert "npm install" in result.output


# ── action: open ──────────────────────────────────────────────


class TestActionOpen:
    @pytest.mark.asyncio
    async def test_invokes_cli(self, tool: BrowserTool, ctx: ToolContext) -> None:
        run = AsyncMock(return_value=_ok())
        with patch(
            "daemon.extensions.tools.builtin.browser.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.browser.run_with_timeout",
            run,
        ):
            result = await tool.execute(
                {"action": "open", "url": "https://example.com"}, ctx
            )

        assert result.is_error is False
        assert "Opened" in result.output
        argv = run.call_args.args[0]
        assert argv[1:] == ["open", "https://example.com"]

    @pytest.mark.asyncio
    async def test_requires_url(self, tool: BrowserTool, ctx: ToolContext) -> None:
        with patch(
            "daemon.extensions.tools.builtin.browser.agent_browser_cli.is_available",
            return_value=True,
        ):
            result = await tool.execute({"action": "open"}, ctx)
        assert result.is_error is True
        assert "url" in result.output.lower()

    @pytest.mark.asyncio
    async def test_runs_domain_filter(self, tool: BrowserTool, ctx: ToolContext) -> None:
        with patch(
            "daemon.extensions.tools.builtin.browser.agent_browser_cli.is_available",
            return_value=True,
        ):
            result = await tool.execute(
                {"action": "open", "url": "http://localhost/"}, ctx
            )
        assert result.is_error is True
        assert "localhost" in result.output

    @pytest.mark.asyncio
    async def test_rejects_non_http(self, tool: BrowserTool, ctx: ToolContext) -> None:
        with patch(
            "daemon.extensions.tools.builtin.browser.agent_browser_cli.is_available",
            return_value=True,
        ):
            result = await tool.execute(
                {"action": "open", "url": "file:///etc/passwd"}, ctx
            )
        assert result.is_error is True
        assert "http(s)" in result.output


# ── action: page ──────────────────────────────────────────────


class TestActionPage:
    @pytest.mark.asyncio
    async def test_uses_interactive_flag(self, tool: BrowserTool, ctx: ToolContext) -> None:
        snapshot = "- heading 'Hi' [ref=e1]"
        run = AsyncMock(return_value=_ok(snapshot))
        with patch(
            "daemon.extensions.tools.builtin.browser.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.browser.run_with_timeout",
            run,
        ):
            result = await tool.execute({"action": "page"}, ctx)

        assert result.is_error is False
        assert result.output == snapshot
        argv = run.call_args.args[0]
        assert argv[1:] == ["snapshot", "-i"]

    @pytest.mark.asyncio
    async def test_failure_returns_error(self, tool: BrowserTool, ctx: ToolContext) -> None:
        run = AsyncMock(return_value=_fail(stderr="no page open"))
        with patch(
            "daemon.extensions.tools.builtin.browser.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.browser.run_with_timeout",
            run,
        ):
            result = await tool.execute({"action": "page"}, ctx)
        assert result.is_error is True
        assert "no page open" in result.output


# ── action: snapshot (screenshot) ─────────────────────────────


class TestActionSnapshot:
    @pytest.mark.asyncio
    async def test_returns_image_parts(
        self, tool: BrowserTool, ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Mock run_with_timeout to write a fake PNG to the path the tool passes.
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 100  # PNG magic + filler

        async def fake_run(cmd, *, cwd, timeout_s, env=None, stdin_bytes=None):
            # cmd[2] is the screenshot path
            Path(cmd[2]).write_bytes(png_bytes)
            return _ok()

        with patch(
            "daemon.extensions.tools.builtin.browser.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.browser.run_with_timeout",
            side_effect=fake_run,
        ):
            result = await tool.execute({"action": "snapshot"}, ctx)

        assert result.is_error is False
        assert result.image_parts is not None
        assert len(result.image_parts) == 1
        img = result.image_parts[0]
        assert img.media_type == "image/png"
        # Round-trip the base64 to check the bytes
        decoded = base64.b64decode(img.data_base64)
        assert decoded == png_bytes

    @pytest.mark.asyncio
    async def test_no_output_returns_error(
        self, tool: BrowserTool, ctx: ToolContext
    ) -> None:
        # Subprocess succeeds but the file is empty (or missing)
        async def fake_run(cmd, *, cwd, timeout_s, env=None, stdin_bytes=None):
            # Don't write anything to the file
            return _ok()

        with patch(
            "daemon.extensions.tools.builtin.browser.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.browser.run_with_timeout",
            side_effect=fake_run,
        ):
            result = await tool.execute({"action": "snapshot"}, ctx)

        assert result.is_error is True
        assert "no output" in result.output.lower()


# ── action: network ───────────────────────────────────────────


class TestActionNetwork:
    @pytest.mark.asyncio
    async def test_returns_request_list(
        self, tool: BrowserTool, ctx: ToolContext
    ) -> None:
        text = "GET https://example.com/api/users 200 application/json"
        run = AsyncMock(return_value=_ok(text))
        with patch(
            "daemon.extensions.tools.builtin.browser.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.browser.run_with_timeout",
            run,
        ):
            result = await tool.execute({"action": "network"}, ctx)

        assert result.is_error is False
        assert result.output == text
        argv = run.call_args.args[0]
        assert argv[1:] == ["network", "requests"]


# ── action: close ─────────────────────────────────────────────


class TestActionClose:
    @pytest.mark.asyncio
    async def test_no_url_required(self, tool: BrowserTool, ctx: ToolContext) -> None:
        run = AsyncMock(return_value=_ok())
        with patch(
            "daemon.extensions.tools.builtin.browser.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.browser.run_with_timeout",
            run,
        ):
            result = await tool.execute({"action": "close"}, ctx)

        assert result.is_error is False
        assert "Closed" in result.output
        argv = run.call_args.args[0]
        assert argv[1:] == ["close"]


# ── Subprocess errors ─────────────────────────────────────────


class TestSubprocessErrors:
    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, tool: BrowserTool, ctx: ToolContext) -> None:
        run = AsyncMock(return_value=_timeout())
        with patch(
            "daemon.extensions.tools.builtin.browser.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.browser.run_with_timeout",
            run,
        ):
            result = await tool.execute(
                {"action": "open", "url": "https://example.com"}, ctx
            )
        assert result.is_error is True
        assert "timed out" in result.output.lower()

    @pytest.mark.asyncio
    async def test_oserror_returns_error(self, tool: BrowserTool, ctx: ToolContext) -> None:
        run = AsyncMock(side_effect=OSError("can't exec"))
        with patch(
            "daemon.extensions.tools.builtin.browser.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.browser.run_with_timeout",
            run,
        ):
            result = await tool.execute(
                {"action": "open", "url": "https://example.com"}, ctx
            )
        assert result.is_error is True
        assert "Failed to launch" in result.output


# ── Input validation ──────────────────────────────────────────


class TestInputValidation:
    def test_invalid_action_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            BrowserTool.Input.model_validate({"action": "drink_coffee"})
