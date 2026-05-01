"""Tests for ExitWorktreeTool."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kernel.git.types import WorktreeSession
from kernel.tools.builtin.exit_worktree import ExitWorktreeTool
from kernel.tools.types import ToolCallResult, ToolInputError


def _make_ws(
    original_cwd: Path = Path("/repo"),
    worktree_path: Path = Path("/repo/.mustang/worktrees/feat"),
) -> WorktreeSession:
    return WorktreeSession(
        session_id="s1",
        original_cwd=original_cwd,
        worktree_path=worktree_path,
        worktree_branch="worktree-feat",
        slug="feat",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _make_ctx(
    *,
    ws: WorktreeSession | None = None,
    git_available: bool = True,
    fire_hook: object | None = None,
    cwd: Path = Path("/repo"),
) -> MagicMock:
    ctx = MagicMock()
    ctx.session_id = "s1"
    ctx.agent_depth = 0
    ctx.cwd = cwd
    ctx.git_manager = MagicMock()
    ctx.git_manager.available = git_available
    ctx.git_manager.get_worktree.return_value = ws
    ctx.git_manager.unregister_worktree = AsyncMock()
    ctx.fire_hook = fire_hook
    return ctx


async def _collect(tool, input, ctx):
    results = []
    async for event in tool.call(input, ctx):
        results.append(event)
    return results


class TestExitWorktree:
    @pytest.mark.asyncio
    async def test_keep_preserves_dir(self) -> None:
        ws = _make_ws()
        ctx = _make_ctx(ws=ws)
        tool = ExitWorktreeTool()
        results = await _collect(tool, {"action": "keep"}, ctx)

        assert len(results) == 1
        assert isinstance(results[0], ToolCallResult)
        assert results[0].data["action"] == "keep"
        assert "kept" in results[0].llm_content[0].text.lower()
        ctx.git_manager.unregister_worktree.assert_awaited_once_with("s1")

    @pytest.mark.asyncio
    async def test_remove_deletes(self) -> None:
        ws = _make_ws()
        ctx = _make_ctx(ws=ws)

        with patch(
            "kernel.tools.builtin.exit_worktree.count_changes",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "kernel.tools.builtin.exit_worktree.remove_worktree",
            new_callable=AsyncMock,
        ) as mock_remove:
            tool = ExitWorktreeTool()
            results = await _collect(tool, {"action": "remove"}, ctx)

        mock_remove.assert_awaited_once()
        assert results[0].data["action"] == "remove"

    @pytest.mark.asyncio
    async def test_remove_with_changes_error(self) -> None:
        ws = _make_ws()
        ctx = _make_ctx(ws=ws)

        with patch(
            "kernel.tools.builtin.exit_worktree.count_changes",
            new_callable=AsyncMock,
            return_value=3,
        ):
            tool = ExitWorktreeTool()
            with pytest.raises(ToolInputError, match="uncommitted change"):
                await _collect(tool, {"action": "remove"}, ctx)

    @pytest.mark.asyncio
    async def test_remove_discard_changes(self) -> None:
        ws = _make_ws()
        ctx = _make_ctx(ws=ws)

        with patch(
            "kernel.tools.builtin.exit_worktree.remove_worktree",
            new_callable=AsyncMock,
        ) as mock_remove:
            tool = ExitWorktreeTool()
            await _collect(
                tool,
                {"action": "remove", "discard_changes": True},
                ctx,
            )

        mock_remove.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_not_in_worktree_error(self) -> None:
        ctx = _make_ctx(ws=None)
        tool = ExitWorktreeTool()
        with pytest.raises(ToolInputError, match="not in a worktree"):
            await _collect(tool, {"action": "keep"}, ctx)

    @pytest.mark.asyncio
    async def test_git_unavailable_without_hook_surfaces_error(self) -> None:
        """No git + no WorktreeRemove hook → tool surfaces a clear error."""
        ctx = _make_ctx(git_available=False)
        tool = ExitWorktreeTool()
        with pytest.raises(ToolInputError, match="not in a worktree session"):
            await _collect(tool, {"action": "keep"}, ctx)

    @pytest.mark.asyncio
    async def test_context_modifier_restores_cwd(self) -> None:

        original = Path("/repo")
        ws = _make_ws(original_cwd=original)
        ctx = _make_ctx(ws=ws)
        tool = ExitWorktreeTool()
        results = await _collect(tool, {"action": "keep"}, ctx)

        modifier = results[0].context_modifier
        from kernel.tools.context import ToolContext
        from kernel.tools.file_state import FileStateCache

        old_ctx = ToolContext(
            session_id="s1",
            agent_depth=0,
            agent_id=None,
            cwd=Path("/repo/.mustang/worktrees/feat"),
            cancel_event=asyncio.Event(),
            file_state=FileStateCache(),
        )
        new_ctx = modifier(old_ctx)
        assert new_ctx.cwd == original

    @pytest.mark.asyncio
    async def test_default_risk_keep_is_low(self) -> None:
        tool = ExitWorktreeTool()
        risk = tool.default_risk({"action": "keep"}, None)
        assert risk.risk == "low"

    @pytest.mark.asyncio
    async def test_default_risk_remove_is_high(self) -> None:
        tool = ExitWorktreeTool()
        risk = tool.default_risk({"action": "remove"}, None)
        assert risk.risk == "high"
