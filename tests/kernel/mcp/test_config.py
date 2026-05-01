"""Tests for kernel.mcp.config — config loading, merging, policy."""

from __future__ import annotations

import json
from pathlib import Path


from kernel.mcp.config import (
    HTTPServerConfig,
    MCPConfig,
    MCPPolicyConfig,
    SSEServerConfig,
    StdioServerConfig,
    WebSocketServerConfig,
    filter_by_policy,
    load_mcp_json,
    merge_configs,
)


# ── Pydantic schema tests ──────────────────────────────────────────


class TestServerConfigs:
    """Config Pydantic models validate correctly."""

    def test_stdio_defaults(self) -> None:
        cfg = StdioServerConfig(command="node")
        assert cfg.type == "stdio"
        assert cfg.args == []
        assert cfg.env == {}

    def test_sse_requires_url(self) -> None:
        cfg = SSEServerConfig(type="sse", url="http://example.com/sse")
        assert cfg.url == "http://example.com/sse"

    def test_http_config(self) -> None:
        cfg = HTTPServerConfig(type="http", url="http://example.com/mcp")
        assert cfg.type == "http"

    def test_ws_config(self) -> None:
        cfg = WebSocketServerConfig(type="ws", url="ws://localhost:8080")
        assert cfg.type == "ws"

    def test_mcp_config_empty(self) -> None:
        cfg = MCPConfig()
        assert cfg.servers == {}

    def test_mcp_config_with_servers(self) -> None:
        cfg = MCPConfig(
            servers={
                "local": StdioServerConfig(command="npx", args=["-y", "server"]),
                "remote": SSEServerConfig(type="sse", url="http://example.com"),
            }
        )
        assert len(cfg.servers) == 2
        assert isinstance(cfg.servers["local"], StdioServerConfig)
        assert isinstance(cfg.servers["remote"], SSEServerConfig)


# ── .mcp.json loading ───────────────────────────────────────────────


class TestLoadMcpJson:
    """load_mcp_json() tests."""

    def test_file_not_found(self, tmp_path: Path) -> None:
        result = load_mcp_json(tmp_path / "nonexistent.json")
        assert result == {}

    def test_valid_stdio(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "echo": {
                            "command": "python",
                            "args": ["-m", "echo_server"],
                            "env": {"KEY": "val"},
                        }
                    }
                }
            )
        )

        result = load_mcp_json(path)

        assert "echo" in result
        cfg = result["echo"]
        assert isinstance(cfg, StdioServerConfig)
        assert cfg.command == "python"
        assert cfg.args == ["-m", "echo_server"]
        assert cfg.env == {"KEY": "val"}

    def test_valid_sse(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "remote": {
                            "type": "sse",
                            "url": "http://mcp.example.com/sse",
                            "headers": {"Authorization": "Bearer tok"},
                        }
                    }
                }
            )
        )

        result = load_mcp_json(path)
        cfg = result["remote"]
        assert isinstance(cfg, SSEServerConfig)
        assert cfg.headers == {"Authorization": "Bearer tok"}

    def test_valid_http(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        path.write_text(
            json.dumps({"mcpServers": {"api": {"type": "http", "url": "http://mcp.example.com"}}})
        )

        result = load_mcp_json(path)
        assert isinstance(result["api"], HTTPServerConfig)

    def test_valid_ws(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        path.write_text(
            json.dumps({"mcpServers": {"ws-server": {"type": "ws", "url": "ws://localhost:9000"}}})
        )

        result = load_mcp_json(path)
        assert isinstance(result["ws-server"], WebSocketServerConfig)

    def test_invalid_json(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        path.write_text("not json")
        assert load_mcp_json(path) == {}

    def test_missing_command_skips(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "bad": {"args": ["x"]},  # no command
                    }
                }
            )
        )
        assert load_mcp_json(path) == {}

    def test_unknown_type_skips(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "x": {"type": "grpc", "url": "foo"},
                    }
                }
            )
        )
        assert load_mcp_json(path) == {}

    def test_empty_mcp_servers(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        path.write_text(json.dumps({"mcpServers": {}}))
        assert load_mcp_json(path) == {}


# ── Merge ───────────────────────────────────────────────────────────


class TestMergeConfigs:
    """merge_configs() priority tests."""

    def test_primary_wins(self) -> None:
        a = {"s": StdioServerConfig(command="a")}
        b = {"s": StdioServerConfig(command="b")}
        merged = merge_configs(a, b)
        assert merged["s"].command == "a"

    def test_no_overlap(self) -> None:
        a = {"x": StdioServerConfig(command="x")}
        b = {"y": StdioServerConfig(command="y")}
        merged = merge_configs(a, b)
        assert set(merged.keys()) == {"x", "y"}

    def test_does_not_mutate_inputs(self) -> None:
        a: dict = {}
        b = {"z": StdioServerConfig(command="z")}
        merged = merge_configs(a, b)
        assert "z" in merged
        assert "z" not in a


# ── Policy filtering ────────────────────────────────────────────────


class TestFilterByPolicy:
    """filter_by_policy() tests."""

    def _servers(self) -> dict[str, StdioServerConfig]:
        return {
            "alpha": StdioServerConfig(command="a"),
            "beta": StdioServerConfig(command="b"),
            "gamma": StdioServerConfig(command="c"),
        }

    def test_no_policy_allows_all(self) -> None:
        allowed, disabled = filter_by_policy(self._servers(), None)
        assert len(allowed) == 3
        assert len(disabled) == 0

    def test_deny_takes_precedence(self) -> None:
        policy = MCPPolicyConfig(
            allowed_servers=["alpha", "beta", "gamma"],
            denied_servers=["beta"],
        )
        allowed, disabled = filter_by_policy(self._servers(), policy)
        assert "beta" in disabled
        assert "beta" not in allowed
        assert len(allowed) == 2

    def test_allow_list_filters(self) -> None:
        policy = MCPPolicyConfig(allowed_servers=["alpha"])
        allowed, disabled = filter_by_policy(self._servers(), policy)
        assert set(allowed.keys()) == {"alpha"}
        assert set(disabled.keys()) == {"beta", "gamma"}

    def test_empty_allow_blocks_all(self) -> None:
        policy = MCPPolicyConfig(allowed_servers=[])
        allowed, disabled = filter_by_policy(self._servers(), policy)
        assert len(allowed) == 0
        assert len(disabled) == 3

    def test_none_allow_permits_all(self) -> None:
        policy = MCPPolicyConfig(allowed_servers=None, denied_servers=[])
        allowed, disabled = filter_by_policy(self._servers(), policy)
        assert len(allowed) == 3
        assert len(disabled) == 0
