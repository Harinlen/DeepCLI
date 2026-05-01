"""Hook-based worktree fallback (CC parity).

Tests the WORKTREE_CREATE / WORKTREE_REMOVE hook fire sites inside
EnterWorktreeTool / ExitWorktreeTool.call() — the non-git path used
when ``ctx.git_manager`` is absent or unavailable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from kernel.hooks.types import (
    EVENT_SPECS,
    HookEvent,
    HookEventCtx,
)
from kernel.tools.builtin.enter_worktree import EnterWorktreeTool
from kernel.tools.builtin.exit_worktree import ExitWorktreeTool
from kernel.tools.types import ToolCallResult, ToolInputError


def _make_ctx(
    *,
    git_available: bool = False,
    fire_hook: Any = None,
    cwd: Path = Path("/repo"),
) -> MagicMock:
    ctx = MagicMock()
    ctx.cwd = cwd
    ctx.session_id = "s1"
    ctx.agent_depth = 0
    if git_available:
        ctx.git_manager = MagicMock()
        ctx.git_manager.available = True
    else:
        ctx.git_manager = None
    ctx.fire_hook = fire_hook
    return ctx


async def _collect(tool: Any, input: dict[str, Any], ctx: Any) -> list[Any]:
    results = []
    async for event in tool.call(input, ctx):
        results.append(event)
    return results


class TestWorktreeEvents:
    def test_worktree_create_in_enum(self) -> None:
        assert HookEvent.WORKTREE_CREATE.value == "worktree_create"

    def test_worktree_remove_in_enum(self) -> None:
        assert HookEvent.WORKTREE_REMOVE.value == "worktree_remove"

    def test_worktree_events_in_specs(self) -> None:
        for ev in (HookEvent.WORKTREE_CREATE, HookEvent.WORKTREE_REMOVE):
            spec = EVENT_SPECS[ev]
            # Worktree handlers are advisory — they never veto the
            # operation; they complete it.
            assert spec.can_block is False


class TestEnterWorktreeHookPath:
    @pytest.mark.asyncio
    async def test_fires_worktree_create_when_git_unavailable(
        self, tmp_path: Path
    ) -> None:
        """No git + hook writes a worktree_path -> tool returns success."""
        captured: list[HookEventCtx] = []

        async def fake_fire(event: HookEvent, event_ctx: HookEventCtx) -> bool:
            captured.append(event_ctx)
            # Simulate a handler that materialised the worktree on disk.
            event_ctx.worktree_handled = True
            event_ctx.worktree_path = str(tmp_path / "hook-wt" / event_ctx.worktree_slug)
            return False  # not blocked

        ctx = _make_ctx(git_available=False, fire_hook=fake_fire)
        tool = EnterWorktreeTool()

        results = await _collect(tool, {"slug": "myfeature"}, ctx)

        assert len(captured) == 1
        assert captured[0].event == HookEvent.WORKTREE_CREATE
        assert captured[0].worktree_slug == "myfeature"

        assert len(results) == 1
        r = results[0]
        assert isinstance(r, ToolCallResult)
        assert r.data["backend"] == "hook"
        assert "myfeature" in r.data["worktree_path"]
        assert r.context_modifier is not None

    @pytest.mark.asyncio
    async def test_no_hook_configured_raises_cc_error(self) -> None:
        """No git + no hook -> CC's exact error message."""
        ctx = _make_ctx(git_available=False, fire_hook=None)
        tool = EnterWorktreeTool()

        with pytest.raises(ToolInputError, match="WorktreeCreate hooks"):
            await _collect(tool, {"slug": "x"}, ctx)

    @pytest.mark.asyncio
    async def test_hook_did_not_handle_raises_cc_error(self) -> None:
        """Hook ran but set no worktree_path -> CC error."""
        async def idle_fire(event: HookEvent, event_ctx: HookEventCtx) -> bool:
            return False  # event_ctx.worktree_handled stays False

        ctx = _make_ctx(git_available=False, fire_hook=idle_fire)
        tool = EnterWorktreeTool()

        with pytest.raises(ToolInputError, match="WorktreeCreate hooks"):
            await _collect(tool, {"slug": "x"}, ctx)


class TestExitWorktreeHookPath:
    @pytest.mark.asyncio
    async def test_fires_worktree_remove_when_git_unavailable(
        self,
    ) -> None:
        captured: list[HookEventCtx] = []

        async def fake_fire(event: HookEvent, event_ctx: HookEventCtx) -> bool:
            captured.append(event_ctx)
            event_ctx.worktree_handled = True
            return False

        ctx = _make_ctx(git_available=False, fire_hook=fake_fire, cwd=Path("/wt"))
        tool = ExitWorktreeTool()

        results = await _collect(tool, {"action": "remove"}, ctx)

        assert len(captured) == 1
        assert captured[0].event == HookEvent.WORKTREE_REMOVE
        assert captured[0].tool_input == {"action": "remove"}
        assert captured[0].worktree_path == "/wt"

        assert len(results) == 1
        r = results[0]
        assert r.data["backend"] == "hook"
        assert r.data["action"] == "remove"

    @pytest.mark.asyncio
    async def test_no_hook_configured_raises(self) -> None:
        ctx = _make_ctx(git_available=False, fire_hook=None)
        tool = ExitWorktreeTool()

        with pytest.raises(ToolInputError, match="not in a worktree session"):
            await _collect(tool, {"action": "keep"}, ctx)

    @pytest.mark.asyncio
    async def test_hook_did_not_handle_raises(self) -> None:
        async def idle_fire(event: HookEvent, event_ctx: HookEventCtx) -> bool:
            return False

        ctx = _make_ctx(git_available=False, fire_hook=idle_fire)
        tool = ExitWorktreeTool()

        with pytest.raises(ToolInputError, match="did not acknowledge"):
            await _collect(tool, {"action": "keep"}, ctx)
