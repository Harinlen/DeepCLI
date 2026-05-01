"""Tests for the extension manager."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from daemon.config.defaults import apply_defaults
from daemon.config.schema import HookRuntimeConfig, SourceConfig
from daemon.extensions.manager import ExtensionManager

# Tools registered by load_builtin_tools() alone.
_CORE_BUILTIN_TOOLS = {
    "agent",
    "ask_user_question",
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
    "http_fetch",
    "memory_write",
    "memory_append",
    "memory_delete",
    "memory_list",
    "page_fetch",
    "web_search",
    "config_tool",
}

# NOTE: http_fetch, page_fetch, and web_search are core tools (CORE_TOOL_NAMES).
# `browser` is a lazy tool — registered as built-in but not in CORE.

# All tools after load_all() (includes tool_search registered in load_all).
BUILTIN_TOOLS = _CORE_BUILTIN_TOOLS | {"tool_search"}


def _write_user_tool(directory: Path, name: str) -> Path:
    """Helper: write a valid user tool file."""
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
    return f


class TestExtensionManager:
    """Tests for ExtensionManager."""

    def test_load_builtin_tools(self) -> None:
        """All built-in tools are registered (excludes tool_search)."""
        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(config)
        mgr.load_builtin_tools()

        assert set(mgr.tool_registry.tool_names) == _CORE_BUILTIN_TOOLS
        assert len(mgr.tool_registry) == len(_CORE_BUILTIN_TOOLS)

    def test_default_tool_context(self) -> None:
        """default_tool_context returns a ToolContext with cwd."""
        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(config)
        ctx = mgr.default_tool_context(cwd="/tmp/test")
        assert ctx.cwd == "/tmp/test"

    def test_default_tool_context_default_cwd(self) -> None:
        """default_tool_context without cwd uses '.'."""
        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(config)
        ctx = mgr.default_tool_context()
        assert ctx.cwd == "."


class TestExtensionManagerUserTools:
    """Tests for user-defined tool loading via ExtensionManager.

    Uses ``skill_dirs=[]`` to isolate from bundled skills.
    """

    @pytest.mark.asyncio
    async def test_load_user_tools(self, tmp_path: Path) -> None:
        """User tools are discovered and registered alongside builtins."""
        _write_user_tool(tmp_path, "custom")
        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(config, user_tools_dir=tmp_path, skill_dirs=[])
        await mgr.load_all()

        assert "custom" in mgr.tool_registry
        assert len(mgr.tool_registry) == len(BUILTIN_TOOLS) + 1  # builtin + 1 user

    @pytest.mark.asyncio
    async def test_user_tool_name_conflict_skipped(self, tmp_path: Path) -> None:
        """User tool with same name as builtin is skipped."""
        _write_user_tool(tmp_path, "bash")  # conflicts with builtin
        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(config, user_tools_dir=tmp_path, skill_dirs=[])
        await mgr.load_all()

        # Still only 6 tools — the conflicting user tool is skipped
        assert len(mgr.tool_registry) == len(BUILTIN_TOOLS)
        assert set(mgr.tool_registry.tool_names) == BUILTIN_TOOLS

    @pytest.mark.asyncio
    async def test_empty_user_tools_dir(self, tmp_path: Path) -> None:
        """Empty user tools directory just loads builtins."""
        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(config, user_tools_dir=tmp_path, skill_dirs=[])
        await mgr.load_all()

        assert set(mgr.tool_registry.tool_names) == BUILTIN_TOOLS

    @pytest.mark.asyncio
    async def test_nonexistent_user_tools_dir(self) -> None:
        """Nonexistent user tools directory just loads builtins."""
        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(config, user_tools_dir=Path("/nonexistent"), skill_dirs=[])
        await mgr.load_all()

        assert set(mgr.tool_registry.tool_names) == BUILTIN_TOOLS

    @pytest.mark.asyncio
    async def test_multiple_user_tools(self, tmp_path: Path) -> None:
        """Multiple user tools are all loaded."""
        _write_user_tool(tmp_path, "alpha")
        _write_user_tool(tmp_path, "beta")
        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(config, user_tools_dir=tmp_path, skill_dirs=[])
        await mgr.load_all()

        assert "alpha" in mgr.tool_registry
        assert "beta" in mgr.tool_registry
        assert len(mgr.tool_registry) == len(BUILTIN_TOOLS) + 2  # builtin + 2 user


class TestExtensionManagerSkills:
    """Tests for skill loading via ExtensionManager."""

    @pytest.mark.asyncio
    async def test_bundled_skills_loaded(self) -> None:
        """Bundled skills are discovered on default load_all()."""
        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(config)
        await mgr.load_all()

        assert "commit" in mgr.skill_registry
        assert "simplify" in mgr.skill_registry
        assert "create-pr" in mgr.skill_registry
        # SkillTool auto-registered
        assert "skill" in mgr.tool_registry

    @pytest.mark.asyncio
    async def test_no_skills_no_skill_tool(self) -> None:
        """When no skills found, SkillTool is not registered."""
        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(config, skill_dirs=[])
        await mgr.load_all()

        assert len(mgr.skill_registry) == 0
        assert "skill" not in mgr.tool_registry

    @pytest.mark.asyncio
    async def test_user_skills_shadow_bundled(self, tmp_path: Path) -> None:
        """User skill with same name as bundled takes precedence."""
        from daemon.extensions.manager import BUNDLED_SKILLS_DIR

        (tmp_path / "commit.md").write_text(
            "---\nname: commit\ndescription: My custom commit\n---\nCustom body"
        )
        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(config, skill_dirs=[tmp_path, BUNDLED_SKILLS_DIR])
        await mgr.load_all()

        skill = mgr.skill_registry.get("commit")
        assert skill is not None
        assert skill.description == "My custom commit"


class TestExtensionManagerHooks:
    """Tests for hook loading via ExtensionManager."""

    def test_load_hooks_from_config(self) -> None:
        """Hooks from config are registered in the hook_registry."""
        raw_config = apply_defaults(SourceConfig())
        # Inject hooks into runtime config
        raw_config = raw_config.model_copy(
            update={
                "hooks": [
                    HookRuntimeConfig(
                        event="pre_tool_use",
                        type="command",
                        if_="Bash(rm *)",
                        command="echo blocked && exit 1",
                    ),
                    HookRuntimeConfig(
                        event="stop",
                        type="command",
                        command="echo done",
                    ),
                ]
            }
        )
        mgr = ExtensionManager(raw_config, skill_dirs=[])
        mgr.load_hooks()

        assert mgr.hook_registry.hook_count == 2

    def test_load_hooks_invalid_event_skipped(self) -> None:
        """Hooks with invalid event are skipped."""
        raw_config = apply_defaults(SourceConfig())
        raw_config = raw_config.model_copy(
            update={
                "hooks": [
                    HookRuntimeConfig(
                        event="invalid_event",
                        type="command",
                        command="echo x",
                    )
                ]
            }
        )
        mgr = ExtensionManager(raw_config, skill_dirs=[])
        mgr.load_hooks()

        assert mgr.hook_registry.hook_count == 0

    def test_load_hooks_invalid_type_skipped(self) -> None:
        """Hooks with invalid type are skipped."""
        raw_config = apply_defaults(SourceConfig())
        raw_config = raw_config.model_copy(
            update={
                "hooks": [
                    HookRuntimeConfig(
                        event="pre_tool_use",
                        type="invalid_type",
                        command="echo x",
                    )
                ]
            }
        )
        mgr = ExtensionManager(raw_config, skill_dirs=[])
        mgr.load_hooks()

        assert mgr.hook_registry.hook_count == 0

    @pytest.mark.asyncio
    async def test_load_all_includes_hooks(self) -> None:
        """load_all() also loads hooks."""
        raw_config = apply_defaults(SourceConfig())
        raw_config = raw_config.model_copy(
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
        mgr = ExtensionManager(raw_config, skill_dirs=[])
        await mgr.load_all()

        assert mgr.hook_registry.hook_count == 1

    @pytest.mark.asyncio
    async def test_no_hooks_empty_registry(self) -> None:
        """No hooks in config → empty hook_registry."""
        config = apply_defaults(SourceConfig())
        mgr = ExtensionManager(config, skill_dirs=[])
        await mgr.load_all()

        assert mgr.hook_registry.hook_count == 0
