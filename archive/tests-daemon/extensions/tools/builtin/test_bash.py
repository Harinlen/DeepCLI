"""Tests for the Bash tool."""

from __future__ import annotations

import pytest

from daemon.extensions.tools.base import PermissionLevel, ToolContext
from daemon.extensions.tools.builtin.bash import BashTool


@pytest.fixture
def tool() -> BashTool:
    return BashTool(default_timeout_ms=5000)


@pytest.fixture
def ctx(tmp_path: object) -> ToolContext:
    return ToolContext(cwd=str(tmp_path))


class TestBashTool:
    """Tests for BashTool."""

    def test_permission_level(self) -> None:
        assert BashTool().permission_level == PermissionLevel.DANGEROUS

    @pytest.mark.asyncio
    async def test_simple_command(self, tool: BashTool, ctx: ToolContext) -> None:
        result = await tool.execute({"command": "echo hello"}, ctx)
        assert result.is_error is False
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_nonzero_exit_code(self, tool: BashTool, ctx: ToolContext) -> None:
        result = await tool.execute({"command": "exit 1"}, ctx)
        assert result.is_error is True
        assert "Exit code: 1" in result.output

    @pytest.mark.asyncio
    async def test_stderr_captured(self, tool: BashTool, ctx: ToolContext) -> None:
        result = await tool.execute({"command": "echo err >&2"}, ctx)
        assert "err" in result.output

    @pytest.mark.asyncio
    async def test_timeout(self, ctx: ToolContext) -> None:
        tool = BashTool(default_timeout_ms=100)
        result = await tool.execute({"command": "sleep 10"}, ctx)
        assert result.is_error is True
        assert "timed out" in result.output.lower()

    @pytest.mark.asyncio
    async def test_cwd_respected(self, tool: BashTool, tmp_path: object) -> None:
        ctx = ToolContext(cwd=str(tmp_path))
        result = await tool.execute({"command": "pwd"}, ctx)
        assert str(tmp_path) in result.output

    @pytest.mark.asyncio
    async def test_no_output(self, tool: BashTool, ctx: ToolContext) -> None:
        result = await tool.execute({"command": "true"}, ctx)
        assert result.is_error is False
        assert result.output == "(no output)"
