"""Tests for EnterPlanModeTool / ExitPlanModeTool."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daemon.extensions.tools.base import PermissionLevel, ToolContext
from daemon.extensions.tools.builtin.enter_plan_mode import EnterPlanModeTool
from daemon.extensions.tools.builtin.exit_plan_mode import ExitPlanModeTool
from daemon.side_effects import EnterPlanMode, ExitPlanMode


def _ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


class TestEnterPlanMode:
    @pytest.mark.asyncio
    async def test_emits_enter_plan_mode_side_effect(self) -> None:
        tool = EnterPlanModeTool()
        result = await tool.execute({}, _ctx())
        assert result.is_error is False
        assert isinstance(result.side_effect, EnterPlanMode)

    def test_permission_level_is_none(self) -> None:
        assert EnterPlanModeTool.permission_level == PermissionLevel.NONE

    @pytest.mark.asyncio
    async def test_output_describes_plan_mode(self) -> None:
        tool = EnterPlanModeTool()
        result = await tool.execute({}, _ctx())
        assert "read-only" in result.output.lower()
        # Verbatim Claude Code text references the tool as "ExitPlanMode"
        assert "ExitPlanMode" in result.output or "exit_plan_mode" in result.output


class TestExitPlanMode:
    @pytest.mark.asyncio
    async def test_emits_exit_plan_mode_side_effect(self) -> None:
        tool = ExitPlanModeTool()
        plan_text = "# Plan\n\n1. Do the thing\n2. Verify"
        result = await tool.execute({"plan": plan_text}, _ctx())
        assert result.is_error is False
        assert isinstance(result.side_effect, ExitPlanMode)
        assert result.side_effect.plan == plan_text

    @pytest.mark.asyncio
    async def test_output_frames_as_approval(self) -> None:
        tool = ExitPlanModeTool()
        result = await tool.execute({"plan": "step one"}, _ctx())
        assert "approved" in result.output.lower()
        assert "step one" in result.output

    @pytest.mark.asyncio
    async def test_rejects_empty_plan(self) -> None:
        tool = ExitPlanModeTool()
        with pytest.raises(ValidationError):
            await tool.execute({"plan": ""}, _ctx())

    def test_permission_level_is_none(self) -> None:
        assert ExitPlanModeTool.permission_level == PermissionLevel.NONE
