"""Tests for user-triggered shell/Python REPL session façade."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from kernel.orchestrator.types import ToolKind
from kernel.llm.types import TextContent, UserMessage
from kernel.protocol.interfaces.contracts.execute_shell_params import ExecuteShellParams
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.session.runtime.state import Session
from kernel.session.user_repl.service import UserReplMixin
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache
from kernel.tools.tool import Tool
from kernel.tools.types import TextDisplay, ToolCallProgress, ToolCallResult


class _StreamingShellTool(Tool[dict[str, Any], str]):
    name = "Bash"
    kind = ToolKind.execute

    async def call(
        self, input: dict[str, Any], ctx: ToolContext
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        yield ToolCallProgress(content=[TextBlock(text="hello\n")])
        yield ToolCallResult(
            data={"exit_code": 0, "stdout": "hello\n", "stderr": ""},
            llm_content=[TextBlock(text="hello")],
            display=TextDisplay(text="hello"),
        )


class _ToolManager:
    def __init__(self) -> None:
        self.tool = _StreamingShellTool()
        self.state = FileStateCache()

    def lookup(self, name: str) -> Tool | None:
        return self.tool if name == "Bash" else None

    def file_state(self) -> FileStateCache:
        return self.state


class _Manager(UserReplMixin):
    def __init__(self, session: Session) -> None:
        self._sessions = {session.session_id: session}
        self._broadcasts: list[Any] = []
        self._module_table = MagicMock()
        self._module_table.get.return_value = _ToolManager()

    async def _get_or_load(self, session_id: str) -> Session:
        return self._sessions[session_id]

    async def _broadcast(self, session: Session, update: Any) -> None:
        self._broadcasts.append(update)

    async def _broadcast_execution_update(self, session: Session, execution: Any) -> None:
        self._broadcasts.append(execution)

    async def _write_event(self, session: Session, event_cls: type, **kwargs: Any) -> str:
        return "ev_test"


class _Orchestrator:
    def __init__(self) -> None:
        self.context: list[str] = []

    def append_user_context(self, text: str) -> UserMessage:
        self.context.append(text)
        return UserMessage(content=[TextContent(text=text)])


def _session(tmp_path: Path) -> Session:
    return Session(
        session_id="s1",
        cwd=tmp_path,
        created_at=MagicMock(),
        updated_at=MagicMock(),
        title=None,
        git_branch=None,
        mode_id=None,
        config_options={},
        mcp_servers=[],
        orchestrator=_Orchestrator(),
    )


@pytest.mark.asyncio
async def test_execute_shell_broadcasts_start_chunk_end(tmp_path: Path) -> None:
    session = _session(tmp_path)
    manager = _Manager(session)

    result = await manager.execute_shell(
        MagicMock(spec=HandlerContext),
        ExecuteShellParams(session_id="s1", command="echo hello"),
    )

    assert result.exit_code == 0
    assert [u.phase for u in manager._broadcasts] == [
        "start",
        "chunk",
        "end",
    ]
    assert manager._broadcasts[1].text == "hello\n"
    assert session.orchestrator.context
    assert "echo hello" in session.orchestrator.context[0]


@pytest.mark.asyncio
async def test_execute_shell_exclude_from_context_skips_history(tmp_path: Path) -> None:
    session = _session(tmp_path)
    manager = _Manager(session)

    await manager.execute_shell(
        MagicMock(spec=HandlerContext),
        ExecuteShellParams(session_id="s1", command="echo hidden", exclude_from_context=True),
    )

    assert session.orchestrator.context == []
