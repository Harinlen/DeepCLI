"""End-to-end integration tests for the extension loading pipeline.

Verifies that all extension types (tools, skills, hooks, MCP) can be
loaded together via ``ExtensionManager.load_all()`` and that the full
lifecycle (load → use → cleanup) works correctly.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.config.defaults import apply_defaults
from daemon.config.schema import HookRuntimeConfig, SourceConfig
from daemon.extensions.manager import ExtensionManager
from daemon.lifecycle import reset_for_testing, run_cleanups

BUILTIN_TOOLS = {
    "agent",
    "bash",
    "browser",
    "file_read",
    "file_write",
    "file_edit",
    "glob",
    "grep",
    "todo_write",
    "enter_plan_mode",
    "exit_plan_mode",
    "memory_write",
    "memory_append",
    "memory_delete",
    "memory_list",
    "http_fetch",
    "page_fetch",
    "web_search",
    "tool_search",
    "ask_user_question",
    "config_tool",
}


@pytest.fixture(autouse=True)
def _clean_lifecycle() -> None:
    """Reset the cleanup registry before each test."""
    reset_for_testing()


def _write_user_tool(directory: Path, name: str) -> None:
    """Write a valid user-defined tool file."""
    f = directory / f"{name}_tool.py"
    f.write_text(
        textwrap.dedent(f"""\
        from typing import Any
        from pydantic import BaseModel
        from daemon.extensions.tools.base import Tool, ToolContext, ToolResult, PermissionLevel

        class {name.title()}Tool(Tool):
            name = "{name}"
            description = "User tool {name}"
            permission_level = PermissionLevel.NONE
            class Input(BaseModel):
                msg: str
            async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
                return ToolResult(output=params.get("msg", ""))
        """)
    )


def _write_skill(directory: Path, name: str) -> None:
    """Write a valid skill file."""
    (directory / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: Skill {name}\n---\nBody for {name}"
    )


def _make_mock_mcp_client(server_name: str, tools: list[dict]) -> MagicMock:
    """Create a mock MCP client with given tools."""
    client = MagicMock()
    client.server_name = server_name
    client.connect = AsyncMock()
    client.close = AsyncMock()
    client.is_connected = True
    client._closing = False
    client._process = None
    client.list_tools = AsyncMock(return_value=tools)
    return client


class TestFullLoadAll:
    """Tests for the complete load_all pipeline with all extension types."""

    @pytest.mark.asyncio
    async def test_all_extensions_loaded(self, tmp_path: Path) -> None:
        """User tool + skill + hook + MCP all load in one call."""
        # Setup user tool
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_user_tool(tools_dir, "custom")

        # Setup skill
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        _write_skill(skills_dir, "deploy")

        # Setup MCP
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"servers": {"fs": {"command": "fake-fs"}}}))

        # Setup hooks
        source = SourceConfig()
        config = apply_defaults(source)
        config = config.model_copy(
            update={
                "hooks": [
                    HookRuntimeConfig(
                        event="stop",
                        type="command",
                        command="echo done",
                    )
                ]
            }
        )

        mgr = ExtensionManager(
            config,
            user_tools_dir=tools_dir,
            skill_dirs=[skills_dir],
            mcp_json_path=mcp_json,
            result_cache_dir=tmp_path / "cache",
        )

        mock_client = _make_mock_mcp_client(
            "fs",
            [{"name": "read_file", "description": "Read a file", "inputSchema": {}}],
        )

        with patch("daemon.extensions.manager.McpClient", return_value=mock_client):
            await mgr.load_all()

        # Verify all registries
        assert "custom" in mgr.tool_registry  # user tool
        assert "mcp__fs__read_file" in mgr.tool_registry  # MCP tool
        assert "skill" in mgr.tool_registry  # SkillTool
        assert BUILTIN_TOOLS <= set(mgr.tool_registry.tool_names)  # builtins
        assert "deploy" in mgr.skill_registry  # skill
        assert mgr.hook_registry.hook_count == 1  # hook

    @pytest.mark.asyncio
    async def test_load_order_builtin_before_user(self, tmp_path: Path) -> None:
        """Built-in tools are loaded before user tools (builtins win conflicts)."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_user_tool(tools_dir, "bash")  # Conflicts with builtin

        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(
            config,
            user_tools_dir=tools_dir,
            skill_dirs=[],
            mcp_json_path=tmp_path / "mcp.json",
            result_cache_dir=tmp_path / "cache",
        )
        await mgr.load_all()

        # bash should be the builtin, not the user tool
        assert "bash" in mgr.tool_registry
        assert len(mgr.tool_registry) == len(BUILTIN_TOOLS)


class TestFullLifecycle:
    """Tests for the complete load → use → cleanup lifecycle."""

    @pytest.mark.asyncio
    async def test_load_and_cleanup(self, tmp_path: Path) -> None:
        """After run_cleanups, MCP tools are removed and clients closed."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"servers": {"srv": {"command": "cmd"}}}))

        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(
            config,
            skill_dirs=[],
            mcp_json_path=mcp_json,
            result_cache_dir=tmp_path / "cache",
        )

        mock_client = _make_mock_mcp_client(
            "srv",
            [{"name": "tool_a", "description": "A", "inputSchema": {}}],
        )

        with patch("daemon.extensions.manager.McpClient", return_value=mock_client):
            await mgr.load_all()

        # Before cleanup: MCP tool present
        assert "mcp__srv__tool_a" in mgr.tool_registry
        # Built-in tools present
        assert BUILTIN_TOOLS <= set(mgr.tool_registry.tool_names)

        # Simulate shutdown
        await run_cleanups()

        # MCP tool removed, built-in tools remain
        assert "mcp__srv__tool_a" not in mgr.tool_registry
        assert BUILTIN_TOOLS <= set(mgr.tool_registry.tool_names)
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_partial_mcp_failure_does_not_block(self, tmp_path: Path) -> None:
        """A failing MCP server does not prevent other extensions from loading."""
        # Setup skill
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        _write_skill(skills_dir, "test-skill")

        # Setup MCP with a server that fails
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"servers": {"broken": {"command": "broken"}}}))

        config = apply_defaults(SourceConfig())
        config = config.model_copy(
            update={"hooks": [HookRuntimeConfig(event="stop", type="command", command="echo x")]}
        )

        mgr = ExtensionManager(
            config,
            skill_dirs=[skills_dir],
            mcp_json_path=mcp_json,
            result_cache_dir=tmp_path / "cache",
        )

        mock_client = MagicMock()
        mock_client.server_name = "broken"
        mock_client.connect = AsyncMock(side_effect=Exception("broken"))
        mock_client.close = AsyncMock()

        with patch("daemon.extensions.manager.McpClient", return_value=mock_client):
            await mgr.load_all()

        # Non-MCP extensions still loaded
        assert BUILTIN_TOOLS <= set(mgr.tool_registry.tool_names)
        assert "test-skill" in mgr.skill_registry
        assert mgr.hook_registry.hook_count == 1
        # No MCP clients connected
        assert len(mgr._mcp_clients) == 0


class TestHealthMonitor:
    """Tests for the MCP health monitor."""

    @pytest.mark.asyncio
    async def test_health_monitor_starts_with_mcp(self, tmp_path: Path) -> None:
        """Health monitor task is created when MCP servers connect."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"servers": {"s": {"command": "c"}}}))

        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(
            config,
            skill_dirs=[],
            mcp_json_path=mcp_json,
            result_cache_dir=tmp_path / "cache",
        )

        mock_client = _make_mock_mcp_client("s", [])

        with patch("daemon.extensions.manager.McpClient", return_value=mock_client):
            await mgr.load_mcp_servers()

        assert mgr._health_task is not None
        assert not mgr._health_task.done()

        # Cleanup cancels it
        await run_cleanups()
        assert mgr._health_task.done()

    @pytest.mark.asyncio
    async def test_no_health_monitor_without_mcp(self, tmp_path: Path) -> None:
        """No health monitor when no MCP servers are configured."""
        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(
            config,
            skill_dirs=[],
            mcp_json_path=tmp_path / "mcp.json",
            result_cache_dir=tmp_path / "cache",
        )
        await mgr.load_mcp_servers()

        assert mgr._health_task is None
