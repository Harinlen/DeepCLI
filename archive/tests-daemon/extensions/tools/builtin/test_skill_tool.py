"""Tests for the SkillTool."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from daemon.extensions.skills.loader import discover_skills
from daemon.extensions.skills.registry import SkillRegistry
from daemon.extensions.tools.base import PermissionLevel, ToolContext
from daemon.extensions.tools.builtin.skill_tool import SkillTool

_SKILL_CONTENT = textwrap.dedent("""\
---
name: "{name}"
description: "Test skill {name}"
---
Prompt for {name}: $ARGUMENTS
""")


def _setup_skill_tool(tmp_path: Path, *names: str) -> SkillTool:
    """Create a SkillTool with skills in tmp_path."""
    for name in names:
        (tmp_path / f"{name}.md").write_text(_SKILL_CONTENT.format(name=name))
    skills = discover_skills([tmp_path])
    reg = SkillRegistry()
    for s in skills:
        reg.register(s)
    return SkillTool(reg)


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


class TestSkillTool:
    """Tests for SkillTool."""

    def test_permission_level(self, tmp_path: Path) -> None:
        tool = _setup_skill_tool(tmp_path, "test")
        assert tool.permission_level == PermissionLevel.NONE

    @pytest.mark.asyncio
    async def test_activate_skill(self, tmp_path: Path, ctx: ToolContext) -> None:
        from daemon.side_effects import SkillActivated

        tool = _setup_skill_tool(tmp_path, "commit")
        result = await tool.execute({"name": "commit", "arguments": "fix login"}, ctx)
        assert result.is_error is False
        assert "Prompt for commit" in result.output
        assert "fix login" in result.output
        # Activation is announced via the side-effect ADT, not a
        # name-based branch in the orchestrator.
        assert isinstance(result.side_effect, SkillActivated)
        assert result.side_effect.prompt == result.output

    @pytest.mark.asyncio
    async def test_error_path_has_no_side_effect(self, tmp_path: Path, ctx: ToolContext) -> None:
        """Failed activation must not emit a SkillActivated effect."""
        tool = _setup_skill_tool(tmp_path, "alpha")
        result = await tool.execute({"name": "nonexistent"}, ctx)
        assert result.is_error is True
        assert result.side_effect is None

    @pytest.mark.asyncio
    async def test_activate_without_arguments(self, tmp_path: Path, ctx: ToolContext) -> None:
        tool = _setup_skill_tool(tmp_path, "review")
        result = await tool.execute({"name": "review"}, ctx)
        assert result.is_error is False
        assert "Prompt for review" in result.output

    @pytest.mark.asyncio
    async def test_skill_not_found(self, tmp_path: Path, ctx: ToolContext) -> None:
        tool = _setup_skill_tool(tmp_path, "alpha")
        result = await tool.execute({"name": "nonexistent"}, ctx)
        assert result.is_error is True
        assert "not found" in result.output.lower()
        assert "alpha" in result.output  # lists available

    @pytest.mark.asyncio
    async def test_empty_registry(self, ctx: ToolContext) -> None:
        tool = SkillTool(SkillRegistry())
        result = await tool.execute({"name": "anything"}, ctx)
        assert result.is_error is True
        assert "(none)" in result.output

    @pytest.mark.asyncio
    async def test_file_deleted_after_discovery(self, tmp_path: Path, ctx: ToolContext) -> None:
        """Graceful error if skill file disappears after discovery."""
        f = tmp_path / "ephemeral.md"
        f.write_text(_SKILL_CONTENT.format(name="ephemeral"))
        tool = _setup_skill_tool(tmp_path, "ephemeral")
        f.unlink()  # delete the file
        result = await tool.execute({"name": "ephemeral"}, ctx)
        assert result.is_error is True
        assert "cannot load" in result.output.lower()

    def test_input_schema(self, tmp_path: Path) -> None:
        tool = _setup_skill_tool(tmp_path, "test")
        schema = tool.input_schema()
        assert "name" in schema["properties"]
        assert "arguments" in schema["properties"]
