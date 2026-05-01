"""Tests for config schema models."""

from daemon.config.schema import (
    DaemonSourceConfig,
    HookSourceConfig,
    McpServerSourceConfig,
    ProviderSourceConfig,
    RuntimeConfig,
    SkillsSourceConfig,
    SourceConfig,
)


class TestSourceConfig:
    def test_empty_source_config(self):
        """SourceConfig with no fields is valid (zero-config)."""
        config = SourceConfig()
        assert config.provider is None
        assert config.daemon is None
        assert config.tools is None
        assert config.skills is None
        assert config.hooks is None
        assert config.mcp_servers is None

    def test_partial_provider_config(self):
        """ProviderSourceConfig allows partial fields."""
        p = ProviderSourceConfig(base_url="http://localhost:8080")
        assert p.base_url == "http://localhost:8080"
        assert p.model is None
        assert p.api_key is None

    def test_partial_daemon_config(self):
        d = DaemonSourceConfig(port=9999)
        assert d.host is None
        assert d.port == 9999

    def test_skills_source_config(self):
        """SkillsSourceConfig accepts disabled list."""
        s = SkillsSourceConfig(disabled=["commit", "review"])
        assert s.disabled == ["commit", "review"]

    def test_hook_source_config(self):
        """HookSourceConfig captures all hook fields."""
        h = HookSourceConfig(
            event="pre_tool_use",
            type="command",
            if_="Bash(rm *)",
            command="echo blocked",
            timeout=10,
        )
        assert h.event == "pre_tool_use"
        assert h.if_ == "Bash(rm *)"
        assert h.timeout == 10

    def test_mcp_server_source_config(self):
        """McpServerSourceConfig captures MCP server definition."""
        m = McpServerSourceConfig(command="npx", args=["-y", "mcp-fs"])
        assert m.type == "stdio"
        assert m.command == "npx"
        assert m.args == ["-y", "mcp-fs"]


class TestRuntimeConfig:
    def test_runtime_config_requires_all_fields(self):
        """RuntimeConfig has no optional fields."""
        from daemon.config.schema import (
            BashToolRuntimeConfig,
            DaemonRuntimeConfig,
            PermissionsRuntimeConfig,
            ProviderRuntimeConfig,
            SessionsRuntimeConfig,
            SkillsRuntimeConfig,
            ToolsRuntimeConfig,
        )

        config = RuntimeConfig(
            default_provider="test",
            providers={
                "test": ProviderRuntimeConfig(
                    type="openai_compatible", base_url="http://localhost", model="m", api_key="k"
                )
            },
            daemon=DaemonRuntimeConfig(host="0.0.0.0", port=8080),
            tools=ToolsRuntimeConfig(bash=BashToolRuntimeConfig(timeout=5000)),
            skills=SkillsRuntimeConfig(disabled=[]),
            hooks=[],
            mcp_servers={},
            sessions=SessionsRuntimeConfig(max_age_days=30, max_count=200, max_file_mb=50),
            permissions=PermissionsRuntimeConfig(mode="default"),
        )
        assert config.default_provider == "test"
        assert config.daemon.port == 8080
        assert config.skills.disabled == []
        assert config.hooks == []
        assert config.mcp_servers == {}
        assert config.sessions.max_age_days == 30
