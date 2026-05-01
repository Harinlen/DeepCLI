"""Tests for PythonTool per-session runtime behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kernel.tools.builtin.python_tool import PythonTool, shutdown_python_worker
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache
from kernel.tools.types import ToolCallResult


def _ctx(tmp_path: Path, session_id: str = "py-test") -> ToolContext:
    return ToolContext(
        session_id=session_id,
        agent_depth=0,
        agent_id=None,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
    )


async def _run(tool: PythonTool, code: str, ctx: ToolContext) -> ToolCallResult:
    result: ToolCallResult | None = None
    async for event in tool.call({"code": code}, ctx):
        if isinstance(event, ToolCallResult):
            result = event
    assert result is not None
    return result


@pytest.mark.asyncio
async def test_python_tool_persists_session_namespace(tmp_path: Path) -> None:
    tool = PythonTool()
    ctx = _ctx(tmp_path)
    try:
        first = await _run(tool, "x = 41", ctx)
        second = await _run(tool, "x + 1", ctx)
    finally:
        shutdown_python_worker(ctx.session_id)

    assert first.data["exit_code"] == 0
    assert second.data["stdout"].strip() == "42"


@pytest.mark.asyncio
async def test_python_tool_reports_traceback(tmp_path: Path) -> None:
    tool = PythonTool()
    ctx = _ctx(tmp_path, "py-error")
    try:
        result = await _run(tool, "1 / 0", ctx)
    finally:
        shutdown_python_worker(ctx.session_id)

    assert result.data["exit_code"] == 1
    assert "ZeroDivisionError" in result.data["stderr"]
