"""Tests for TodoWriteTool — stateless side-effect producer."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daemon.extensions.tools.base import ToolContext
from daemon.extensions.tools.builtin.todo_write import TodoWriteTool
from daemon.side_effects import TasksUpdated


def _ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


class TestTodoWriteTool:
    """Execute path — basic invariants."""

    @pytest.mark.asyncio
    async def test_returns_tasks_updated_side_effect(self) -> None:
        tool = TodoWriteTool()
        result = await tool.execute(
            {
                "todos": [
                    {"content": "A", "status": "pending", "active_form": "Doing A"},
                    {
                        "content": "B",
                        "status": "in_progress",
                        "active_form": "Doing B",
                    },
                ]
            },
            _ctx(),
        )
        assert result.is_error is False
        assert isinstance(result.side_effect, TasksUpdated)
        assert len(result.side_effect.tasks) == 2
        assert result.side_effect.tasks[0].content == "A"

    @pytest.mark.asyncio
    async def test_summary_counts_statuses(self) -> None:
        tool = TodoWriteTool()
        result = await tool.execute(
            {
                "todos": [
                    {"content": "a", "status": "pending", "active_form": "A"},
                    {"content": "b", "status": "completed", "active_form": "B"},
                    {"content": "c", "status": "completed", "active_form": "C"},
                    {"content": "d", "status": "in_progress", "active_form": "D"},
                ]
            },
            _ctx(),
        )
        assert "1 pending" in result.output
        assert "1 in progress" in result.output
        assert "2 completed" in result.output
        assert "4 total" in result.output

    @pytest.mark.asyncio
    async def test_empty_list_is_clear_message(self) -> None:
        tool = TodoWriteTool()
        result = await tool.execute({"todos": []}, _ctx())
        assert "cleared" in result.output.lower()
        assert isinstance(result.side_effect, TasksUpdated)
        assert result.side_effect.tasks == []

    @pytest.mark.asyncio
    async def test_rejects_empty_content(self) -> None:
        tool = TodoWriteTool()
        with pytest.raises(ValidationError):
            await tool.execute(
                {
                    "todos": [
                        {"content": "", "status": "pending", "active_form": "A"},
                    ]
                },
                _ctx(),
            )

    @pytest.mark.asyncio
    async def test_rejects_invalid_status(self) -> None:
        tool = TodoWriteTool()
        with pytest.raises(ValidationError):
            await tool.execute(
                {
                    "todos": [
                        {"content": "x", "status": "blocked", "active_form": "X"},
                    ]
                },
                _ctx(),
            )

    def test_tool_has_none_permission_level(self) -> None:
        from daemon.extensions.tools.base import PermissionLevel

        assert TodoWriteTool.permission_level == PermissionLevel.NONE
