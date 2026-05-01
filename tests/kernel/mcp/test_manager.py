"""Tests for kernel.mcp.MCPManager subsystem."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from kernel.config import ConfigManager
from kernel.flags import FlagManager
from kernel.mcp import MCPManager
from kernel.mcp.config import HTTPServerConfig
from kernel.mcp.types import ConnectedServer, FailedServer, McpError
from kernel.module_table import KernelModuleTable


@pytest.fixture
async def module_table(tmp_path: Path) -> KernelModuleTable:
    """Minimal module table with empty config."""
    flags = FlagManager(path=tmp_path / "flags.yaml")
    await flags.initialize()

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    project_dir = tmp_path / "project-config"
    project_dir.mkdir()

    config = ConfigManager(
        global_dir=config_dir,
        project_dir=project_dir,
        cli_overrides=(),
    )
    await config.startup()

    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    return KernelModuleTable(flags=flags, config=config, state_dir=state_dir)


@pytest.mark.anyio
async def test_startup_no_config(module_table: KernelModuleTable) -> None:
    """MCPManager starts cleanly with no MCP servers configured."""
    mgr = MCPManager(module_table)
    await mgr.startup()

    assert mgr.get_connected() == []
    assert mgr.get_connections() == {}

    await mgr.shutdown()


@pytest.mark.anyio
async def test_startup_with_failed_server(
    module_table: KernelModuleTable,
    tmp_path: Path,
) -> None:
    """A bad server config results in FailedServer, not a crash."""
    # Write a .mcp.json with a non-existent command.
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "bad-server": {
                        "command": "/nonexistent/binary",
                        "args": [],
                    }
                }
            }
        )
    )

    mgr = MCPManager(module_table)
    with patch("kernel.mcp.Path") as mock_path_cls:
        # Make Path.cwd() return tmp_path so .mcp.json is found.
        mock_path_cls.cwd.return_value = tmp_path
        mock_path_cls.side_effect = Path  # allow Path(...) to work normally
        # Patch at the module level where it's used.
        with patch("kernel.mcp.load_mcp_json") as mock_load:
            from kernel.mcp.config import load_mcp_json as real_load

            mock_load.return_value = real_load(mcp_json)
            await mgr.startup()

    connections = mgr.get_connections()
    assert "bad-server" in connections
    conn = connections["bad-server"]
    assert isinstance(conn, FailedServer)
    assert mgr.get_connected() == []

    await mgr.shutdown()


@pytest.mark.anyio
async def test_signal_emits_on_startup(module_table: KernelModuleTable) -> None:
    """on_tools_changed signal fires during startup."""
    mgr = MCPManager(module_table)
    callback = AsyncMock()
    mgr.on_tools_changed.connect(callback)

    await mgr.startup()

    callback.assert_awaited_once()

    await mgr.shutdown()


@pytest.mark.anyio
async def test_shutdown_idempotent(module_table: KernelModuleTable) -> None:
    """Multiple shutdown() calls don't raise."""
    mgr = MCPManager(module_table)
    await mgr.startup()
    await mgr.shutdown()
    await mgr.shutdown()  # no error


@pytest.mark.anyio
async def test_list_tools_not_connected(module_table: KernelModuleTable) -> None:
    """list_tools() returns empty list for non-existent server."""
    mgr = MCPManager(module_table)
    await mgr.startup()

    tools = await mgr.list_tools("nonexistent")
    assert tools == []

    await mgr.shutdown()


@pytest.mark.anyio
async def test_connect_failure_warning_rate_limited(
    module_table: KernelModuleTable,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Repeated connect failures produce at most _FAIL_LOG_LIMIT warnings.

    First two attempts log plainly; the third announces suppression; any
    subsequent attempts are silent until a successful reconnect logs a
    recovery message and the counter resets.
    """
    import logging

    from kernel.mcp import _FAIL_LOG_LIMIT

    mgr = MCPManager(module_table)
    await mgr.startup()

    cfg = HTTPServerConfig(type="http", url="https://never-resolves.invalid/mcp")
    mgr._configs["flaky"] = cfg

    # Fail N+2 times — warnings should cap at _FAIL_LOG_LIMIT.
    failure_attempts = _FAIL_LOG_LIMIT + 2
    with patch("kernel.mcp.create_transport", side_effect=McpError("boom")):
        caplog.set_level(logging.WARNING, logger="kernel.mcp")
        for _ in range(failure_attempts):
            await mgr.reconnect("flaky")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == _FAIL_LOG_LIMIT, (
        f"expected {_FAIL_LOG_LIMIT} warnings, got {len(warnings)}:\n"
        + "\n".join(r.getMessage() for r in warnings)
    )
    assert "suppressing further warnings" in warnings[-1].getMessage()
    assert mgr._fail_counts["flaky"] == failure_attempts

    # Simulate recovery: _connect_one should log INFO and reset the counter.
    caplog.clear()
    caplog.set_level(logging.INFO, logger="kernel.mcp")
    fake_transport = AsyncMock()
    fake_client = AsyncMock()
    fake_client.connect.return_value = {}
    fake_client.server_info = {}
    fake_client.instructions = None
    with (
        patch("kernel.mcp.create_transport", return_value=fake_transport),
        patch("kernel.mcp.McpClient", return_value=fake_client),
    ):
        conn = await mgr.reconnect("flaky")

    assert isinstance(conn, ConnectedServer)
    assert "flaky" not in mgr._fail_counts
    info_msgs = [
        r.getMessage() for r in caplog.records if r.levelno == logging.INFO
    ]
    assert any("reconnected after" in m for m in info_msgs), info_msgs

    await mgr.shutdown()
