"""SkillTool — validate, call, error paths.

Tests the actual ToolCallResult construction to catch missing fields
(like the ``display`` parameter bug that was missed in the first pass).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from kernel.tools.builtin.skill_tool import SkillTool
from kernel.tools.types import TextDisplay, ToolCallResult, ToolInputError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill_tool() -> SkillTool:
    return SkillTool()


def _make_tool_context(
    *,
    skills_mgr: Any = None,
    has_skills: bool = True,
) -> MagicMock:
    """Build a mock ToolContext with an optional SkillManager."""
    ctx = MagicMock()
    ctx.cwd = Path("/tmp")
    ctx.session_id = "test-session"

    module_table = MagicMock()
    ctx.module_table = module_table

    if skills_mgr is not None:
        module_table.has.return_value = True
        module_table.get.return_value = skills_mgr
    elif has_skills:
        module_table.has.return_value = False
    else:
        module_table.has.return_value = False

    return ctx


def _make_skills_mgr(
    *,
    skills: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a mock SkillManager."""
    mgr = MagicMock()

    if skills is None:
        skills = {}

    def _lookup(name: str) -> Any:
        return skills.get(name)

    mgr.lookup.side_effect = _lookup

    return mgr


def _make_skill(
    name: str = "test-skill",
    *,
    body: str = "Skill body content",
    disable_model_invocation: bool = False,
    allowed_tools: tuple[str, ...] = (),
    hooks: dict | None = None,
    setup_needed: bool = False,
    setup_message: str | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Return (loaded_skill_mock, activation_result_mock)."""
    manifest = MagicMock()
    manifest.name = name
    manifest.disable_model_invocation = disable_model_invocation
    manifest.allowed_tools = allowed_tools
    manifest.hooks = hooks

    skill = MagicMock()
    skill.manifest = manifest

    from kernel.skills.types import ActivationResult

    result = ActivationResult(
        body=body,
        allowed_tools=allowed_tools,
        model=None,
        context=None,
        agent=None,
        hooks=hooks,
        skill_root="/tmp/test-skill",
        setup_needed=setup_needed,
        setup_message=setup_message,
    )

    return skill, result


async def _collect_results(tool: SkillTool, input: dict, ctx: Any) -> list[Any]:
    """Run tool.call() and collect all yielded results."""
    results = []
    async for item in tool.call(input, ctx):
        results.append(item)
    return results


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------


class TestValidateInput:
    @pytest.mark.asyncio
    async def test_empty_skill_name_raises(self) -> None:
        tool = _make_skill_tool()
        ctx = MagicMock()
        ctx.cwd = Path("/tmp")
        ctx.session_id = "s"
        with pytest.raises(ToolInputError, match="non-empty"):
            await tool.validate_input({"skill": ""}, ctx)

    @pytest.mark.asyncio
    async def test_slash_prefix_stripped(self) -> None:
        tool = _make_skill_tool()
        ctx = MagicMock()
        ctx.cwd = Path("/tmp")
        ctx.session_id = "s"
        # /name should be normalized to "name" — no error.
        await tool.validate_input({"skill": "/test"}, ctx)

    @pytest.mark.asyncio
    async def test_valid_name_passes(self) -> None:
        tool = _make_skill_tool()
        ctx = MagicMock()
        ctx.cwd = Path("/tmp")
        ctx.session_id = "s"
        await tool.validate_input({"skill": "my-skill"}, ctx)


# ---------------------------------------------------------------------------
# call — happy path
# ---------------------------------------------------------------------------


class TestCallHappyPath:
    @pytest.mark.asyncio
    async def test_successful_activation_returns_body(self) -> None:
        """The critical test: ToolCallResult is constructed with all
        required fields including ``display``."""
        skill, activation = _make_skill(body="Hello from skill")
        mgr = _make_skills_mgr(skills={"test-skill": skill})
        mgr.activate.return_value = activation

        tool = _make_skill_tool()
        ctx = _make_tool_context(skills_mgr=mgr)

        results = await _collect_results(tool, {"skill": "test-skill"}, ctx)

        assert len(results) == 1
        result = results[0]
        assert isinstance(result, ToolCallResult)
        assert result.data["success"] is True
        assert result.data["commandName"] == "test-skill"
        # Verify display field is present and correct type.
        assert isinstance(result.display, TextDisplay)
        # Verify body is in LLM content.
        assert any("Hello from skill" in b.text for b in result.llm_content)

    @pytest.mark.asyncio
    async def test_activation_with_args(self) -> None:
        skill, activation = _make_skill()
        mgr = _make_skills_mgr(skills={"s": skill})
        mgr.activate.return_value = activation

        tool = _make_skill_tool()
        ctx = _make_tool_context(skills_mgr=mgr)

        results = await _collect_results(tool, {"skill": "s", "args": "hello"}, ctx)

        mgr.activate.assert_called_once_with("s", "hello")
        assert len(results) == 1
        assert results[0].data["success"] is True


# ---------------------------------------------------------------------------
# call — error paths
# ---------------------------------------------------------------------------


class TestCallErrors:
    @pytest.mark.asyncio
    async def test_unknown_skill(self) -> None:
        mgr = _make_skills_mgr()
        tool = _make_skill_tool()
        ctx = _make_tool_context(skills_mgr=mgr)

        results = await _collect_results(tool, {"skill": "nonexistent"}, ctx)

        assert len(results) == 1
        result = results[0]
        assert isinstance(result, ToolCallResult)
        assert result.data["success"] is False
        assert "Unknown skill" in result.llm_content[0].text
        assert isinstance(result.display, TextDisplay)

    @pytest.mark.asyncio
    async def test_disable_model_invocation(self) -> None:
        skill, _ = _make_skill(disable_model_invocation=True)
        mgr = _make_skills_mgr(skills={"blocked": skill})

        tool = _make_skill_tool()
        ctx = _make_tool_context(skills_mgr=mgr)

        results = await _collect_results(tool, {"skill": "blocked"}, ctx)

        assert len(results) == 1
        assert results[0].data["success"] is False
        assert "disable-model-invocation" in results[0].llm_content[0].text
        assert isinstance(results[0].display, TextDisplay)

    @pytest.mark.asyncio
    async def test_skills_manager_unavailable(self) -> None:
        tool = _make_skill_tool()
        ctx = _make_tool_context(has_skills=False)

        results = await _collect_results(tool, {"skill": "any"}, ctx)

        assert len(results) == 1
        assert results[0].data["success"] is False
        assert "not available" in results[0].llm_content[0].text
        assert isinstance(results[0].display, TextDisplay)

    @pytest.mark.asyncio
    async def test_activate_returns_none(self) -> None:
        skill, _ = _make_skill()
        mgr = _make_skills_mgr(skills={"broken": skill})
        mgr.activate.return_value = None

        tool = _make_skill_tool()
        ctx = _make_tool_context(skills_mgr=mgr)

        results = await _collect_results(tool, {"skill": "broken"}, ctx)

        assert len(results) == 1
        assert results[0].data["success"] is False
        assert isinstance(results[0].display, TextDisplay)


# ---------------------------------------------------------------------------
# call — setup needed (Hermes flow)
# ---------------------------------------------------------------------------


class TestSetupNeeded:
    @pytest.mark.asyncio
    async def test_setup_needed_returns_message(self) -> None:
        skill, activation = _make_skill(
            setup_needed=True,
            setup_message="Please set API_KEY",
        )
        mgr = _make_skills_mgr(skills={"needs-setup": skill})
        mgr.activate.return_value = activation

        tool = _make_skill_tool()
        ctx = _make_tool_context(skills_mgr=mgr)

        results = await _collect_results(tool, {"skill": "needs-setup"}, ctx)

        assert len(results) == 1
        result = results[0]
        assert result.data["success"] is False
        assert result.data["setup_needed"] is True
        assert "API_KEY" in result.llm_content[0].text
        assert isinstance(result.display, TextDisplay)


# ---------------------------------------------------------------------------
# default_risk
# ---------------------------------------------------------------------------


class TestDefaultRisk:
    def test_safe_skill_auto_allows(self) -> None:
        skill, _ = _make_skill()
        mgr = _make_skills_mgr(skills={"safe": skill})
        tool = _make_skill_tool()
        ctx = _make_tool_context(skills_mgr=mgr)

        risk = tool.default_risk({"skill": "safe"}, ctx)
        assert risk.default_decision == "allow"

    def test_skill_with_allowed_tools_asks(self) -> None:
        skill, _ = _make_skill(allowed_tools=("Bash(npm *)",))
        mgr = _make_skills_mgr(skills={"risky": skill})
        tool = _make_skill_tool()
        ctx = _make_tool_context(skills_mgr=mgr)

        risk = tool.default_risk({"skill": "risky"}, ctx)
        assert risk.default_decision == "ask"

    def test_unknown_skill_allows(self) -> None:
        mgr = _make_skills_mgr()
        tool = _make_skill_tool()
        ctx = _make_tool_context(skills_mgr=mgr)

        risk = tool.default_risk({"skill": "nope"}, ctx)
        assert risk.default_decision == "allow"
