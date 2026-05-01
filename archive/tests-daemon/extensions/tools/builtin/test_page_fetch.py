"""Tests for PageFetchTool — heavily mocked since CI has no Chrome."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from daemon.extensions.tools.base import PermissionLevel, ToolContext
from daemon.extensions.tools.builtin.page_fetch import PageFetchTool
from daemon.extensions.tools.builtin.subprocess_utils import SubprocessResult


@pytest.fixture
def tool() -> PageFetchTool:
    return PageFetchTool()


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


def _ok(stdout: str = "", stderr: str = "") -> SubprocessResult:
    return SubprocessResult(stdout=stdout, stderr=stderr, returncode=0)


def _fail(stderr: str = "boom", returncode: int = 1) -> SubprocessResult:
    return SubprocessResult(stdout="", stderr=stderr, returncode=returncode)


def _timeout() -> SubprocessResult:
    return SubprocessResult(stdout="", stderr="", returncode=-1, timed_out=True)


# ── Validation ────────────────────────────────────────────────


class TestValidation:
    def test_permission_level(self, tool: PageFetchTool) -> None:
        assert tool.permission_level == PermissionLevel.PROMPT

    @pytest.mark.asyncio
    async def test_rejects_non_http(self, tool: PageFetchTool, ctx: ToolContext) -> None:
        result = await tool.execute({"url": "file:///etc/passwd"}, ctx)
        assert result.is_error is True
        assert "http(s)" in result.output

    @pytest.mark.asyncio
    async def test_rejects_missing_host(self, tool: PageFetchTool, ctx: ToolContext) -> None:
        result = await tool.execute({"url": "https://"}, ctx)
        assert result.is_error is True
        assert "host" in result.output.lower()

    @pytest.mark.asyncio
    async def test_rejects_localhost(self, tool: PageFetchTool, ctx: ToolContext) -> None:
        result = await tool.execute({"url": "http://localhost/"}, ctx)
        assert result.is_error is True
        assert "localhost" in result.output


# ── CLI availability ──────────────────────────────────────────


class TestCliAvailability:
    @pytest.mark.asyncio
    async def test_missing_cli_returns_actionable_error(
        self, tool: PageFetchTool, ctx: ToolContext
    ) -> None:
        with patch(
            "daemon.extensions.tools.builtin.page_fetch.agent_browser_cli.is_available",
            return_value=False,
        ):
            result = await tool.execute({"url": "https://example.com"}, ctx)
        assert result.is_error is True
        assert "npm install" in result.output


# ── Subprocess interaction ───────────────────────────────────


class TestSubprocessFlow:
    @pytest.mark.asyncio
    async def test_calls_open_then_snapshot(
        self, tool: PageFetchTool, ctx: ToolContext
    ) -> None:
        run = AsyncMock(side_effect=[_ok(), _ok("- heading 'Example' [ref=e1]")])
        with patch(
            "daemon.extensions.tools.builtin.page_fetch.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.page_fetch.run_with_timeout",
            run,
        ):
            result = await tool.execute({"url": "https://example.com"}, ctx)

        assert result.is_error is False
        # Two subprocess invocations: open then snapshot
        assert run.call_count == 2
        first_argv = run.call_args_list[0].args[0]
        second_argv = run.call_args_list[1].args[0]
        assert first_argv[1:] == ["open", "https://example.com"]
        assert second_argv[1:] == ["snapshot", "-i"]

    @pytest.mark.asyncio
    async def test_returns_snapshot_text(
        self, tool: PageFetchTool, ctx: ToolContext
    ) -> None:
        snapshot_text = "- heading 'Hello' [ref=e1]\n- link 'More' [ref=e2]"
        run = AsyncMock(side_effect=[_ok(), _ok(snapshot_text)])
        with patch(
            "daemon.extensions.tools.builtin.page_fetch.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.page_fetch.run_with_timeout",
            run,
        ):
            result = await tool.execute({"url": "https://example.com"}, ctx)

        assert result.is_error is False
        assert result.output == snapshot_text

    @pytest.mark.asyncio
    async def test_truncates_to_max_chars(
        self, tool: PageFetchTool, ctx: ToolContext
    ) -> None:
        long_text = "x" * 5000
        run = AsyncMock(side_effect=[_ok(), _ok(long_text)])
        with patch(
            "daemon.extensions.tools.builtin.page_fetch.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.page_fetch.run_with_timeout",
            run,
        ):
            result = await tool.execute(
                {"url": "https://example.com", "max_chars": 100}, ctx
            )

        assert result.is_error is False
        assert "truncated" in result.output

    @pytest.mark.asyncio
    async def test_open_failure_returns_error(
        self, tool: PageFetchTool, ctx: ToolContext
    ) -> None:
        run = AsyncMock(side_effect=[_fail(stderr="open failed")])
        with patch(
            "daemon.extensions.tools.builtin.page_fetch.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.page_fetch.run_with_timeout",
            run,
        ):
            result = await tool.execute({"url": "https://example.com"}, ctx)

        assert result.is_error is True
        assert "open failed" in result.output

    @pytest.mark.asyncio
    async def test_snapshot_failure_returns_error(
        self, tool: PageFetchTool, ctx: ToolContext
    ) -> None:
        run = AsyncMock(side_effect=[_ok(), _fail(stderr="snapshot failed")])
        with patch(
            "daemon.extensions.tools.builtin.page_fetch.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.page_fetch.run_with_timeout",
            run,
        ):
            result = await tool.execute({"url": "https://example.com"}, ctx)

        assert result.is_error is True
        assert "snapshot failed" in result.output

    @pytest.mark.asyncio
    async def test_open_timeout_returns_error(
        self, tool: PageFetchTool, ctx: ToolContext
    ) -> None:
        run = AsyncMock(side_effect=[_timeout()])
        with patch(
            "daemon.extensions.tools.builtin.page_fetch.agent_browser_cli.is_available",
            return_value=True,
        ), patch(
            "daemon.extensions.tools.builtin.page_fetch.run_with_timeout",
            run,
        ):
            result = await tool.execute({"url": "https://example.com"}, ctx)

        assert result.is_error is True
        assert "timed out" in result.output.lower()
