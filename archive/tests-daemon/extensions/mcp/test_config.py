"""Tests for MCP config loading."""

from __future__ import annotations

import json
from pathlib import Path

from daemon.config.schema import McpServerRuntimeConfig
from daemon.extensions.mcp.config import load_mcp_config


class TestLoadMcpConfig:
    """Tests for load_mcp_config()."""

    def test_empty_both_sources(self, tmp_path: Path) -> None:
        """No config from either source returns empty list."""
        result = load_mcp_config(tmp_path / "mcp.json", {})
        assert result == []

    def test_from_config_yaml_only(self, tmp_path: Path) -> None:
        """Servers from config.yaml are loaded."""
        servers = {
            "test-server": McpServerRuntimeConfig(
                type="stdio",
                command="echo",
                args=["hello"],
                env={"FOO": "bar"},
            )
        }
        result = load_mcp_config(tmp_path / "mcp.json", servers)
        assert len(result) == 1
        assert result[0].name == "test-server"
        assert result[0].command == "echo"
        assert result[0].args == ["hello"]
        assert result[0].env == {"FOO": "bar"}

    def test_from_mcp_json_only(self, tmp_path: Path) -> None:
        """Servers from mcp.json are loaded."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            json.dumps(
                {
                    "servers": {
                        "fs": {
                            "type": "stdio",
                            "command": "npx",
                            "args": ["-y", "@anthropic/mcp-fs"],
                        }
                    }
                }
            )
        )
        result = load_mcp_config(mcp_json, {})
        assert len(result) == 1
        assert result[0].name == "fs"
        assert result[0].command == "npx"

    def test_mcp_json_flat_format(self, tmp_path: Path) -> None:
        """mcp.json without 'servers' wrapper also works."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            json.dumps(
                {
                    "fs": {
                        "type": "stdio",
                        "command": "npx",
                    }
                }
            )
        )
        result = load_mcp_config(mcp_json, {})
        assert len(result) == 1
        assert result[0].name == "fs"

    def test_mcp_json_overrides_config_yaml(self, tmp_path: Path) -> None:
        """mcp.json entry overrides config.yaml with same name."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            json.dumps({"servers": {"srv": {"command": "from-json", "args": ["--json"]}}})
        )
        config_servers = {
            "srv": McpServerRuntimeConfig(
                type="stdio",
                command="from-yaml",
                args=["--yaml"],
                env={},
            )
        }
        result = load_mcp_config(mcp_json, config_servers)
        assert len(result) == 1
        assert result[0].command == "from-json"

    def test_merge_different_names(self, tmp_path: Path) -> None:
        """Different server names from both sources are merged."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"servers": {"alpha": {"command": "a"}}}))
        config_servers = {
            "beta": McpServerRuntimeConfig(type="stdio", command="b", args=[], env={})
        }
        result = load_mcp_config(mcp_json, config_servers)
        names = {e.name for e in result}
        assert names == {"alpha", "beta"}

    def test_unsupported_type_skipped(self, tmp_path: Path) -> None:
        """Unsupported transport types are warned and skipped."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            json.dumps({"servers": {"remote": {"type": "grpc", "url": "localhost:50051"}}})
        )
        result = load_mcp_config(mcp_json, {})
        assert len(result) == 0

    def test_sse_type_accepted(self, tmp_path: Path) -> None:
        """SSE transport type is accepted when url is provided."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            json.dumps({"servers": {"remote": {"type": "sse", "url": "http://localhost:3000"}}})
        )
        result = load_mcp_config(mcp_json, {})
        assert len(result) == 1
        assert result[0].type == "sse"
        assert result[0].url == "http://localhost:3000"

    def test_inprocess_type_accepted(self, tmp_path: Path) -> None:
        """Inprocess type is accepted when module and class are provided."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            json.dumps(
                {
                    "servers": {
                        "local": {
                            "type": "inprocess",
                            "module": "my.server",
                            "class": "MyServer",
                        }
                    }
                }
            )
        )
        result = load_mcp_config(mcp_json, {})
        assert len(result) == 1
        assert result[0].type == "inprocess"
        assert result[0].module == "my.server"
        assert result[0].class_name == "MyServer"

    def test_inprocess_missing_module_skipped(self, tmp_path: Path) -> None:
        """Inprocess without module is skipped."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"servers": {"bad": {"type": "inprocess"}}}))
        result = load_mcp_config(mcp_json, {})
        assert len(result) == 0

    def test_sse_missing_url_skipped(self, tmp_path: Path) -> None:
        """SSE without url is skipped."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"servers": {"bad": {"type": "sse"}}}))
        result = load_mcp_config(mcp_json, {})
        assert len(result) == 0

    def test_missing_command_skipped(self, tmp_path: Path) -> None:
        """Entry without command is skipped."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"servers": {"broken": {"type": "stdio"}}}))
        result = load_mcp_config(mcp_json, {})
        assert len(result) == 0

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        """Malformed mcp.json is gracefully handled."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text("{invalid json")
        result = load_mcp_config(mcp_json, {})
        assert result == []

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """Missing mcp.json is gracefully handled."""
        result = load_mcp_config(tmp_path / "no-such-file.json", {})
        assert result == []

    def test_default_type_is_stdio(self, tmp_path: Path) -> None:
        """Entries without explicit type default to stdio."""
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(json.dumps({"servers": {"srv": {"command": "test-cmd"}}}))
        result = load_mcp_config(mcp_json, {})
        assert len(result) == 1
        assert result[0].type == "stdio"
