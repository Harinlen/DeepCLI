"""Tests for config defaults and apply_defaults()."""

from daemon.config.defaults import apply_defaults
from daemon.config.schema import (
    DaemonSourceConfig,
    HookSourceConfig,
    McpServerSourceConfig,
    PermissionsSourceConfig,
    ProviderSourceConfig,
    SessionsSourceConfig,
    SkillsSourceConfig,
    SourceConfig,
)


class TestApplyDefaults:
    def test_empty_source_gets_all_defaults(self):
        """Zero-config: all defaults applied including new sections."""
        result = apply_defaults(SourceConfig())
        assert result.default_provider == "local"
        assert "local" in result.providers
        assert result.providers["local"].base_url == "http://127.0.0.1:8080/v1"
        assert result.providers["local"].model == "qwen3.5"
        assert result.daemon.host == "127.0.0.1"
        assert result.daemon.port == 7777
        assert result.tools.bash.timeout == 120_000
        assert result.skills.disabled == []
        assert result.hooks == []
        assert result.mcp_servers == {}
        assert result.sessions.max_age_days == 30
        assert result.permissions.mode == "default"

    def test_custom_provider_preserved(self):
        """User-defined providers are kept, defaults fill gaps."""
        source = SourceConfig(
            provider={
                "default": "custom",
                "custom": ProviderSourceConfig(
                    base_url="http://my-server:5000/v1",
                    model="my-model",
                    api_key="my-key",
                ),
            }
        )
        result = apply_defaults(source)
        assert result.default_provider == "custom"
        assert result.providers["custom"].base_url == "http://my-server:5000/v1"
        assert result.providers["custom"].model == "my-model"
        assert result.providers["custom"].api_key == "my-key"

    def test_missing_default_provider_gets_local(self):
        """If default provider name not in providers dict, local is added."""
        source = SourceConfig(provider={"default": "missing"})
        result = apply_defaults(source)
        # "missing" doesn't exist as a config block, so fallback local is created
        assert "missing" in result.providers

    def test_custom_daemon_config(self):
        source = SourceConfig(daemon=DaemonSourceConfig(port=9999))
        result = apply_defaults(source)
        assert result.daemon.port == 9999
        assert result.daemon.host == "127.0.0.1"  # default

    def test_partial_provider_gets_defaults(self):
        """Provider with only base_url gets default model and api_key."""
        source = SourceConfig(
            provider={
                "default": "partial",
                "partial": ProviderSourceConfig(base_url="http://custom:8080/v1"),
            }
        )
        result = apply_defaults(source)
        p = result.providers["partial"]
        assert p.base_url == "http://custom:8080/v1"
        assert p.model == "qwen3.5"  # default
        assert p.api_key == "no-key"  # default


class TestApplyDefaultsSkills:
    """Tests for skills section defaults."""

    def test_skills_disabled_preserved(self):
        source = SourceConfig(skills=SkillsSourceConfig(disabled=["commit", "review"]))
        result = apply_defaults(source)
        assert result.skills.disabled == ["commit", "review"]

    def test_skills_empty_disabled(self):
        source = SourceConfig(skills=SkillsSourceConfig(disabled=[]))
        result = apply_defaults(source)
        assert result.skills.disabled == []

    def test_skills_none_gets_empty_list(self):
        source = SourceConfig(skills=SkillsSourceConfig())
        result = apply_defaults(source)
        assert result.skills.disabled == []


class TestApplyDefaultsHooks:
    """Tests for hooks section defaults."""

    def test_hooks_resolved(self):
        """Hook source config is resolved with defaults."""
        source = SourceConfig(
            hooks=[
                HookSourceConfig(event="pre_tool_use", type="command", command="echo hi"),
            ]
        )
        result = apply_defaults(source)
        assert len(result.hooks) == 1
        h = result.hooks[0]
        assert h.event == "pre_tool_use"
        assert h.type == "command"
        assert h.command == "echo hi"
        assert h.timeout == 30  # default
        assert h.async_ is False  # default

    def test_hook_custom_timeout(self):
        source = SourceConfig(
            hooks=[
                HookSourceConfig(event="stop", type="http", url="https://x.com", timeout=5),
            ]
        )
        result = apply_defaults(source)
        assert result.hooks[0].timeout == 5

    def test_hook_if_condition_preserved(self):
        source = SourceConfig(
            hooks=[
                HookSourceConfig(
                    event="pre_tool_use",
                    type="command",
                    if_="Bash(rm *)",
                    command="exit 1",
                ),
            ]
        )
        result = apply_defaults(source)
        assert result.hooks[0].if_ == "Bash(rm *)"

    def test_multiple_hooks(self):
        source = SourceConfig(
            hooks=[
                HookSourceConfig(event="pre_tool_use", type="command", command="a"),
                HookSourceConfig(event="stop", type="http", url="https://x.com"),
            ]
        )
        result = apply_defaults(source)
        assert len(result.hooks) == 2


class TestApplyDefaultsMcpServers:
    """Tests for mcp_servers section defaults."""

    def test_mcp_server_resolved(self):
        source = SourceConfig(
            mcp_servers={
                "fs": McpServerSourceConfig(
                    command="npx", args=["-y", "mcp-fs"], env={"HOME": "/tmp"}
                ),
            }
        )
        result = apply_defaults(source)
        assert "fs" in result.mcp_servers
        srv = result.mcp_servers["fs"]
        assert srv.command == "npx"
        assert srv.args == ["-y", "mcp-fs"]
        assert srv.env == {"HOME": "/tmp"}
        assert srv.type == "stdio"

    def test_mcp_server_missing_command_skipped(self):
        """MCP servers without a command are filtered out."""
        source = SourceConfig(
            mcp_servers={"bad": McpServerSourceConfig()},
        )
        result = apply_defaults(source)
        assert result.mcp_servers == {}

    def test_mcp_server_defaults_for_optional_fields(self):
        """Args and env default to empty when not provided."""
        source = SourceConfig(
            mcp_servers={"minimal": McpServerSourceConfig(command="my-server")},
        )
        result = apply_defaults(source)
        srv = result.mcp_servers["minimal"]
        assert srv.args == []
        assert srv.env == {}

    # -- Sessions config --------------------------------------------------

    def test_sessions_defaults(self):
        """Empty sessions config gets default limits."""
        result = apply_defaults(SourceConfig())
        assert result.sessions.max_age_days == 30
        assert result.sessions.max_count == 200
        assert result.sessions.max_file_mb == 50

    def test_sessions_user_overrides(self):
        """User can override individual session limits."""
        source = SourceConfig(
            sessions=SessionsSourceConfig(max_age_days=7, max_count=50),
        )
        result = apply_defaults(source)
        assert result.sessions.max_age_days == 7
        assert result.sessions.max_count == 50
        assert result.sessions.max_file_mb == 50  # default fallback


class TestPermissionsDefaults:
    """Permission mode resolution (Step 4.6)."""

    def test_default_mode_when_unset(self):
        """Absent permissions section → ``default`` mode."""
        result = apply_defaults(SourceConfig())
        assert result.permissions.mode == "default"

    def test_prompt_alias_maps_to_default(self):
        """MVP alias ``prompt`` is coerced to ``default``."""
        source = SourceConfig(permissions=PermissionsSourceConfig(mode="prompt"))
        result = apply_defaults(source)
        assert result.permissions.mode == "default"

    def test_valid_modes_preserved(self):
        """All valid mode strings survive resolution."""
        for mode in ("default", "accept_edits", "plan", "bypass"):
            source = SourceConfig(permissions=PermissionsSourceConfig(mode=mode))
            result = apply_defaults(source)
            assert result.permissions.mode == mode

    def test_unknown_mode_falls_back(self):
        """Unknown mode strings silently fall back to default."""
        source = SourceConfig(permissions=PermissionsSourceConfig(mode="turbo"))
        result = apply_defaults(source)
        assert result.permissions.mode == "default"

    def test_case_insensitive(self):
        """Mode coercion is case-insensitive."""
        source = SourceConfig(permissions=PermissionsSourceConfig(mode="PLAN"))
        result = apply_defaults(source)
        assert result.permissions.mode == "plan"


class TestCancelledToolPolicy:
    """cancelled_tool_policy coercion (Phase 4.X cancel hardening)."""

    def test_default_when_unset(self):
        """Absent sessions config → policy defaults to ``acknowledge``."""
        result = apply_defaults(SourceConfig())
        assert result.sessions.cancelled_tool_policy == "acknowledge"

    def test_valid_policies_preserved(self):
        for policy in ("acknowledge", "hide", "verbatim"):
            source = SourceConfig(sessions=SessionsSourceConfig(cancelled_tool_policy=policy))
            result = apply_defaults(source)
            assert result.sessions.cancelled_tool_policy == policy

    def test_unknown_policy_falls_back(self):
        source = SourceConfig(sessions=SessionsSourceConfig(cancelled_tool_policy="yoloose"))
        result = apply_defaults(source)
        assert result.sessions.cancelled_tool_policy == "acknowledge"

    def test_case_insensitive(self):
        source = SourceConfig(sessions=SessionsSourceConfig(cancelled_tool_policy="HIDE"))
        result = apply_defaults(source)
        assert result.sessions.cancelled_tool_policy == "hide"
