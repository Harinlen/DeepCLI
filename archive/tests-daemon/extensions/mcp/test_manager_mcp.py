"""Tests for MCP integration in ExtensionManager."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.config.defaults import apply_defaults
from daemon.config.schema import SourceConfig
from daemon.extensions.manager import ExtensionManager
from daemon.lifecycle import reset_for_testing, run_cleanups


@pytest.fixture(autouse=True)
def _clean_lifecycle() -> None:
    """Reset the cleanup registry before each test."""
    reset_for_testing()


def _mock_transport(connected: bool = True) -> MagicMock:
    """Create a mock Transport that reports as connected."""
    transport = MagicMock()
    transport.connect = AsyncMock()
    transport.send = AsyncMock()
    transport.close = AsyncMock()
    transport.is_connected = connected
    return transport


def _mock_client(
    name: str = "test",
    *,
    tools: list[dict] | None = None,
    connect_error: Exception | None = None,
) -> MagicMock:
    """Create a mock McpClient with standard defaults."""
    client = MagicMock()
    client.server_name = name
    client.close = AsyncMock()
    client.is_connected = True
    client.transport = _mock_transport()

    if connect_error:
        client.connect = AsyncMock(side_effect=connect_error)
    else:
        client.connect = AsyncMock()

    client.list_tools = AsyncMock(return_value=tools or [])
    return client


class TestExtensionManagerMcp:
    """Tests for MCP server loading in ExtensionManager."""

    @pytest.mark.asyncio
    async def test_load_all_no_mcp(self, tmp_path: Path) -> None:
        """load_all with no MCP config still works."""
        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(
            config,
            skill_dirs=[],
            mcp_json_path=tmp_path / "mcp.json",
            result_cache_dir=tmp_path / "cache",
        )
        await mgr.load_all()

        assert mgr._mcp_clients == []
        assert mgr._mcp_bridges == []

    @pytest.mark.asyncio
    async def test_load_mcp_servers_success(self, tmp_path: Path) -> None:
        """MCP servers are connected and tools registered."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"servers": {"test": {"command": "fake-cmd"}}}))

        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(
            config,
            skill_dirs=[],
            mcp_json_path=mcp_json,
            result_cache_dir=tmp_path / "cache",
        )
        mgr.load_builtin_tools()

        mock_client = _mock_client(
            "test",
            tools=[{"name": "read", "description": "Read", "inputSchema": {}}],
        )

        with (
            patch(
                "daemon.extensions.manager.create_transport",
                return_value=_mock_transport(),
            ),
            patch(
                "daemon.extensions.manager.McpClient",
                return_value=mock_client,
            ),
        ):
            await mgr.load_mcp_servers()

        assert len(mgr._mcp_clients) == 1
        assert "mcp__test__read" in mgr.tool_registry

    @pytest.mark.asyncio
    async def test_load_mcp_servers_failure_continues(self, tmp_path: Path) -> None:
        """Failed MCP servers don't block other servers."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            json.dumps(
                {
                    "servers": {
                        "good": {"command": "good-cmd"},
                        "bad": {"command": "bad-cmd"},
                    }
                }
            )
        )

        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(
            config,
            skill_dirs=[],
            mcp_json_path=mcp_json,
            result_cache_dir=tmp_path / "cache",
        )
        mgr.load_builtin_tools()

        def make_client(transport: object, **kwargs: object) -> MagicMock:
            name = kwargs.get("server_name", "unknown")
            if name == "bad":
                return _mock_client(
                    str(name),
                    connect_error=Exception("connection failed"),
                )
            return _mock_client(
                str(name),
                tools=[{"name": "tool1", "description": "T", "inputSchema": {}}],
            )

        with (
            patch(
                "daemon.extensions.manager.create_transport",
                return_value=_mock_transport(),
            ),
            patch(
                "daemon.extensions.manager.McpClient",
                side_effect=make_client,
            ),
        ):
            await mgr.load_mcp_servers()

        # Only the "good" server should be connected
        assert len(mgr._mcp_clients) == 1
        assert mgr._mcp_clients[0].server_name == "good"

    @pytest.mark.asyncio
    async def test_cleanup_closes_clients_and_removes_tools(self, tmp_path: Path) -> None:
        """run_cleanups() closes MCP clients and unregisters proxy tools."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"servers": {"srv": {"command": "cmd"}}}))

        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(
            config,
            skill_dirs=[],
            mcp_json_path=mcp_json,
            result_cache_dir=tmp_path / "cache",
        )
        mgr.load_builtin_tools()

        mock_client = _mock_client(
            "srv",
            tools=[{"name": "mytool", "description": "A tool", "inputSchema": {}}],
        )

        with (
            patch(
                "daemon.extensions.manager.create_transport",
                return_value=_mock_transport(),
            ),
            patch(
                "daemon.extensions.manager.McpClient",
                return_value=mock_client,
            ),
        ):
            await mgr.load_mcp_servers()

        assert "mcp__srv__mytool" in mgr.tool_registry

        await run_cleanups()

        mock_client.close.assert_awaited_once()
        assert "mcp__srv__mytool" not in mgr.tool_registry

    @pytest.mark.asyncio
    async def test_cleanup_on_startup_called(self, tmp_path: Path) -> None:
        """Result store cleanup runs during load_all()."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "stale.txt").write_text("old")

        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(
            config,
            skill_dirs=[],
            mcp_json_path=tmp_path / "mcp.json",
            result_cache_dir=cache_dir,
        )

        await mgr.load_all()

        assert not (cache_dir / "stale.txt").exists()

    @pytest.mark.asyncio
    async def test_mcp_from_config_yaml(self, tmp_path: Path) -> None:
        """MCP servers from config.yaml mcp_servers section."""
        source = SourceConfig(
            mcp_servers={
                "yaml-srv": {
                    "command": "yaml-cmd",
                    "args": ["--test"],
                }
            }
        )
        config = apply_defaults(source)
        mgr = ExtensionManager(
            config,
            skill_dirs=[],
            mcp_json_path=tmp_path / "mcp.json",
            result_cache_dir=tmp_path / "cache",
        )
        mgr.load_builtin_tools()

        mock_client = _mock_client("yaml-srv")

        with (
            patch(
                "daemon.extensions.manager.create_transport",
                return_value=_mock_transport(),
            ),
            patch(
                "daemon.extensions.manager.McpClient",
                return_value=mock_client,
            ),
        ):
            await mgr.load_mcp_servers()

        assert len(mgr._mcp_clients) == 1

    @pytest.mark.asyncio
    async def test_on_reconnect_wired(self, tmp_path: Path) -> None:
        """Client's on_reconnect is wired to bridge.sync_tools."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"servers": {"s": {"command": "c"}}}))

        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(
            config,
            skill_dirs=[],
            mcp_json_path=mcp_json,
            result_cache_dir=tmp_path / "cache",
        )
        mgr.load_builtin_tools()

        mock_client = _mock_client("s")

        with (
            patch(
                "daemon.extensions.manager.create_transport",
                return_value=_mock_transport(),
            ),
            patch(
                "daemon.extensions.manager.McpClient",
                return_value=mock_client,
            ),
        ):
            await mgr.load_mcp_servers()

        assert mock_client.on_reconnect is not None
