"""Tests for context_modifier pipeline in ToolExecutor."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kernel.orchestrator.tool_executor import ToolExecutor
from kernel.orchestrator.types import OrchestratorDeps, ToolKind
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache
from kernel.tools.types import (
    ToolCallResult,
    TextDisplay,
)
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.llm.types import ToolUseContent


def _make_deps(**overrides: Any) -> OrchestratorDeps:
    defaults = {
        "provider": MagicMock(),
        "tool_source": None,
        "authorizer": None,
        "should_avoid_prompts_provider": None,
        "memory": None,
        "skills": None,
        "hooks": None,
        "set_mode": None,
        "queue_reminders": None,
        "drain_reminders": None,
        "prompts": None,
        "task_registry": None,
        "deliver_cross_session": None,
        "schedule_manager": None,
        "git": None,
    }
    defaults.update(overrides)
    return OrchestratorDeps(**defaults)


def _make_tool_ctx(cwd: Path = Path("/cwd")) -> ToolContext:
    return ToolContext(
        session_id="s1",
        agent_depth=0,
        agent_id=None,
        cwd=cwd,
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
    )


def _make_auth_ctx() -> Any:
    from kernel.tool_authz.types import AuthorizeContext

    return AuthorizeContext(
        session_id="s1",
        cwd=Path("/cwd"),
        agent_depth=0,
        mode="default",
        connection_auth=MagicMock(),
    )


def _make_authorizer() -> MagicMock:
    from kernel.tool_authz.types import PermissionAllow, ReasonMode

    auth = MagicMock()
    allow = PermissionAllow(
        decision_reason=ReasonMode(mode="bypass"),
        updated_input=None,
    )
    auth.authorize = AsyncMock(return_value=allow)
    return auth


def _make_tool(*, context_modifier=None, name: str = "TestTool") -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.kind = ToolKind.execute
    tool.should_defer = False
    tool.is_read_only = False
    tool.is_concurrency_safe = False
    tool.max_result_size_chars = 100_000

    result = ToolCallResult(
        data={},
        llm_content=[TextBlock(text="ok")],
        display=TextDisplay(text="ok"),
        context_modifier=context_modifier,
    )

    async def _call(inp, ctx):
        yield result

    tool.call = _call
    tool.validate_input = AsyncMock()
    tool.default_risk = MagicMock(return_value=None)
    tool.user_facing_name = MagicMock(return_value=name)
    tool.activity_description = MagicMock(return_value=None)
    tool.prepare_permission_matcher = MagicMock(return_value=None)
    tool.is_destructive = MagicMock(return_value=False)
    return tool


class TestContextModifier:
    @pytest.mark.asyncio
    async def test_modifier_updates_cwd(self) -> None:
        new_cwd = Path("/new/cwd")
        captured: list[Path] = []

        def on_change(new_ctx: ToolContext) -> None:
            captured.append(new_ctx.cwd)

        def modifier(ctx: ToolContext) -> ToolContext:
            return dataclasses.replace(ctx, cwd=new_cwd)

        authorizer = _make_authorizer()
        deps = _make_deps(authorizer=authorizer)
        executor = ToolExecutor(
            deps=deps,
            session_id="s1",
            cwd=Path("/old/cwd"),
            on_context_changed=on_change,
        )

        tool = _make_tool(context_modifier=modifier)
        tc = ToolUseContent(id="tc1", name="TestTool", input={"a": 1})

        events = []
        async for event in executor._run_one(
            tc=tc,
            tool=tool,
            tool_ctx=_make_tool_ctx(),
            auth_ctx=_make_auth_ctx(),
            authorizer=authorizer,
            on_permission=AsyncMock(),
            mode="default",
        ):
            events.append(event)

        assert len(captured) == 1
        assert captured[0] == new_cwd

    @pytest.mark.asyncio
    async def test_modifier_none_is_noop(self) -> None:
        captured: list[bool] = []

        def on_change(new_ctx: ToolContext) -> None:
            captured.append(True)

        authorizer = _make_authorizer()
        deps = _make_deps(authorizer=authorizer)
        executor = ToolExecutor(
            deps=deps,
            session_id="s1",
            cwd=Path("/cwd"),
            on_context_changed=on_change,
        )

        tool = _make_tool(context_modifier=None)
        tc = ToolUseContent(id="tc2", name="TestTool", input={})

        async for _ in executor._run_one(
            tc=tc,
            tool=tool,
            tool_ctx=_make_tool_ctx(),
            auth_ctx=_make_auth_ctx(),
            authorizer=authorizer,
            on_permission=AsyncMock(),
            mode="default",
        ):
            pass

        assert len(captured) == 0

    @pytest.mark.asyncio
    async def test_modifier_exception_caught(self) -> None:
        """A failing context_modifier should not prevent tool result emission."""

        def bad_modifier(ctx: ToolContext) -> ToolContext:
            raise ValueError("boom")

        authorizer = _make_authorizer()
        deps = _make_deps(authorizer=authorizer)
        executor = ToolExecutor(
            deps=deps,
            session_id="s1",
            cwd=Path("/cwd"),
            on_context_changed=lambda ctx: None,
        )

        tool = _make_tool(context_modifier=bad_modifier)
        tc = ToolUseContent(id="tc3", name="TestTool", input={})

        events = []
        async for event in executor._run_one(
            tc=tc,
            tool=tool,
            tool_ctx=_make_tool_ctx(),
            auth_ctx=_make_auth_ctx(),
            authorizer=authorizer,
            on_permission=AsyncMock(),
            mode="default",
        ):
            events.append(event)

        # Should still have produced events (ToolCallStart + ToolCallResult).
        assert len(events) >= 2
