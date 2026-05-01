"""Tests for TodoWriteTool."""

import asyncio
from pathlib import Path

import pytest

from kernel.tasks.registry import TaskRegistry
from kernel.tools.builtin.todo_write import TodoWriteTool
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache
from kernel.tools.types import ToolInputError


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="test",
        agent_depth=0,
        agent_id=None,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
        tasks=TaskRegistry(),
    )


def _todo(content: str, status: str = "pending", active: str | None = None) -> dict[str, str]:
    """Helper: build a valid todo dict with default activeForm."""
    return {
        "content": content,
        "activeForm": active or f"{content}ing",
        "status": status,
    }


class TestTodoWriteTool:
    @pytest.mark.asyncio
    async def test_set_todos(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        tool = TodoWriteTool()
        todos = [
            _todo("fix bug", "pending", "Fixing bug"),
            _todo("write tests", "in_progress", "Writing tests"),
        ]
        results = []
        async for event in tool.call({"todos": todos}, ctx):
            results.append(event)

        assert len(results) == 1
        assert results[0].data["new_todos"] == todos
        assert ctx.tasks.get_todos(None) == todos  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_auto_clear_all_completed(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        tool = TodoWriteTool()
        todos = [
            _todo("done1", "completed", "Doing 1"),
            _todo("done2", "completed", "Doing 2"),
        ]
        results = []
        async for event in tool.call({"todos": todos}, ctx):
            results.append(event)

        assert results[0].data["new_todos"] == []
        assert ctx.tasks.get_todos(None) == []  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_returns_old_todos(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        old = [_todo("old task", "pending", "Doing old task")]
        ctx.tasks.set_todos(None, old)  # type: ignore[union-attr]

        tool = TodoWriteTool()
        new = [_todo("new task", "in_progress", "Doing new task")]
        results = []
        async for event in tool.call({"todos": new}, ctx):
            results.append(event)

        assert results[0].data["old_todos"] == old

    @pytest.mark.asyncio
    async def test_no_registry_graceful(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        ctx.tasks = None
        tool = TodoWriteTool()
        results = []
        async for event in tool.call({"todos": [_todo("x")]}, ctx):
            results.append(event)

        # Should not crash
        assert len(results) == 1


class TestTodoWriteValidation:
    """activeForm / content / status are required — bad inputs must raise."""

    @pytest.mark.asyncio
    async def test_rejects_missing_active_form(self, tmp_path: Path) -> None:
        tool = TodoWriteTool()
        bad = [{"content": "fix bug", "status": "pending"}]
        with pytest.raises(ToolInputError, match="activeForm"):
            await tool.validate_input({"todos": bad}, None)

    @pytest.mark.asyncio
    async def test_rejects_empty_active_form(self, tmp_path: Path) -> None:
        tool = TodoWriteTool()
        bad = [{"content": "fix", "activeForm": "   ", "status": "pending"}]
        with pytest.raises(ToolInputError, match="activeForm"):
            await tool.validate_input({"todos": bad}, None)

    @pytest.mark.asyncio
    async def test_rejects_missing_content(self, tmp_path: Path) -> None:
        tool = TodoWriteTool()
        bad = [{"activeForm": "Fixing bug", "status": "pending"}]
        with pytest.raises(ToolInputError, match="content"):
            await tool.validate_input({"todos": bad}, None)

    @pytest.mark.asyncio
    async def test_rejects_invalid_status(self, tmp_path: Path) -> None:
        tool = TodoWriteTool()
        bad = [{"content": "x", "activeForm": "Xing", "status": "waiting"}]
        with pytest.raises(ToolInputError, match="status"):
            await tool.validate_input({"todos": bad}, None)

    @pytest.mark.asyncio
    async def test_accepts_valid_input(self, tmp_path: Path) -> None:
        tool = TodoWriteTool()
        good = [_todo("fix bug", "pending", "Fixing bug")]
        # Should not raise
        await tool.validate_input({"todos": good}, None)

    def test_schema_marks_active_form_required(self) -> None:
        schema = TodoWriteTool().to_schema().input_schema
        item = schema["properties"]["todos"]["items"]
        assert "activeForm" in item["required"]
        assert "content" in item["required"]
        assert "status" in item["required"]
