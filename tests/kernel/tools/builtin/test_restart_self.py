"""Tests for RestartSelfTool."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kernel.tools.builtin.restart_self import RestartSelfTool
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="test",
        agent_depth=0,
        agent_id=None,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
    )


@pytest.mark.asyncio
async def test_restart_self_returns_scheduled_metadata(tmp_path: Path) -> None:
    tool = RestartSelfTool()
    results = []

    async for event in tool.call({"reason": "reload skills"}, _ctx(tmp_path)):
        results.append(event)

    assert len(results) == 1
    result = results[0]
    assert result.data == {"agent_id": "primary", "reason": "reload skills"}
    assert result.meta["mustang.agent/restartSelf"]["agentId"] == "primary"  # type: ignore[index]
    assert "Self-restart scheduled" in result.llm_content[0].text  # type: ignore[attr-defined]
