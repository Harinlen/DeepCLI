from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kernel.agents.mustang.tools.builtin.repl_python import ReplTool
from kernel.agents.mustang.tools.context import ToolContext
from kernel.agents.mustang.tools.file_state import FileStateCache
from kernel.agents.mustang.tools.types import NestedToolResult, ToolCallResult


def _ctx(tmp_path: Path, run_nested_tool: Any) -> ToolContext:
    return ToolContext(
        session_id="s",
        agent_depth=0,
        agent_id=None,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
        run_nested_tool=run_nested_tool,
    )


@pytest.mark.asyncio
async def test_repl_tool_runs_code_and_returns_value(tmp_path: Path) -> None:
    tool = ReplTool()

    async def run_nested_tool(name: str, input: dict[str, Any]) -> NestedToolResult:
        return NestedToolResult(tool_name=name, text="unused")

    events = [
        event
        async for event in tool.call(
            {"code": "print('hi')\no = 3"},
            _ctx(tmp_path, run_nested_tool),
        )
    ]
    try:
        assert len(events) == 1
        result = events[0]
        assert isinstance(result, ToolCallResult)
        text = result.llm_content[0].text
        assert "stdout:" in text
        assert "hi" in text
        assert "return:" in text
        assert "3" in text
    finally:
        await tool.shutdown()


@pytest.mark.asyncio
async def test_repl_tool_uses_nested_dispatch(tmp_path: Path) -> None:
    tool = ReplTool()
    seen: list[tuple[str, dict[str, Any]]] = []

    async def run_nested_tool(name: str, input: dict[str, Any]) -> NestedToolResult:
        seen.append((name, input))
        return NestedToolResult(tool_name=name, text="nested output")

    events = [
        event
        async for event in tool.call(
            {"code": 'o = await Read(file_path="x.txt")'},
            _ctx(tmp_path, run_nested_tool),
        )
    ]
    try:
        assert seen == [("Read", {"file_path": "x.txt", "__repl_cwd": str(tmp_path)})]
        result = events[0]
        assert isinstance(result, ToolCallResult)
        assert result.data["value"] == "nested output"
    finally:
        await tool.shutdown()
