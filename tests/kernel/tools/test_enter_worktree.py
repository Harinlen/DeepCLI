"""Tests for EnterWorktreeTool."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kernel.tools.builtin.enter_worktree import EnterWorktreeTool
from kernel.tools.types import ToolCallResult, ToolInputError


def _make_ctx(
    *,
    cwd: Path = Path("/repo"),
    session_id: str = "s1",
    git_available: bool = True,
    worktree_exists: bool = False,
    fire_hook: object | None = None,
) -> MagicMock:
    ctx = MagicMock()
    ctx.cwd = cwd
    ctx.session_id = session_id
    ctx.agent_depth = 0
    ctx.git_manager = MagicMock()
    ctx.git_manager.available = git_available

    if worktree_exists:
        ctx.git_manager.get_worktree.return_value = MagicMock()
    else:
        ctx.git_manager.get_worktree.return_value = None

    ctx.git_manager.register_worktree = AsyncMock()
    # None (default) means "no hook subsystem" — use an explicit None so
    # MagicMock's auto-attribute doesn't masquerade as a live closure.
    ctx.fire_hook = fire_hook
    return ctx


async def _collect(tool, input, ctx):
    results = []
    async for event in tool.call(input, ctx):
        results.append(event)
    return results


class TestEnterWorktree:
    @pytest.mark.asyncio
    async def test_creates_and_registers(self, tmp_path: Path) -> None:
        wt_path = tmp_path / ".mustang" / "worktrees" / "feat"
        ctx = _make_ctx(cwd=tmp_path)

        with (
            patch(
                "kernel.tools.builtin.enter_worktree.find_git_root",
                new_callable=AsyncMock,
                return_value=tmp_path,
            ),
            patch(
                "kernel.tools.builtin.enter_worktree.create_worktree",
                new_callable=AsyncMock,
                return_value=(wt_path, "worktree-feat"),
            ),
        ):
            tool = EnterWorktreeTool()
            results = await _collect(tool, {"slug": "feat"}, ctx)

        assert len(results) == 1
        result = results[0]
        assert isinstance(result, ToolCallResult)
        assert result.data["worktree_path"] == str(wt_path)
        assert result.data["branch"] == "worktree-feat"
        assert result.context_modifier is not None
        ctx.git_manager.register_worktree.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_slug_validation_empty(self) -> None:
        ctx = _make_ctx()
        tool = EnterWorktreeTool()
        with pytest.raises(ToolInputError, match="1-64 characters"):
            await _collect(tool, {"slug": ""}, ctx)

    @pytest.mark.asyncio
    async def test_slug_validation_too_long(self) -> None:
        ctx = _make_ctx()
        tool = EnterWorktreeTool()
        with pytest.raises(ToolInputError, match="1-64 characters"):
            await _collect(tool, {"slug": "a" * 65}, ctx)

    @pytest.mark.asyncio
    async def test_slug_validation_dotdot(self) -> None:
        ctx = _make_ctx()
        tool = EnterWorktreeTool()
        with pytest.raises(ToolInputError, match="must not contain"):
            await _collect(tool, {"slug": "../escape"}, ctx)

    @pytest.mark.asyncio
    async def test_slug_validation_special_chars(self) -> None:
        ctx = _make_ctx()
        tool = EnterWorktreeTool()
        with pytest.raises(ToolInputError, match="invalid characters"):
            await _collect(tool, {"slug": "feat@bad"}, ctx)

    @pytest.mark.asyncio
    async def test_already_in_worktree_error(self) -> None:
        ctx = _make_ctx(worktree_exists=True)
        tool = EnterWorktreeTool()
        with pytest.raises(ToolInputError, match="already in a worktree"):
            await _collect(tool, {"slug": "feat"}, ctx)

    @pytest.mark.asyncio
    async def test_not_git_repo_error(self) -> None:
        ctx = _make_ctx()
        with patch(
            "kernel.tools.builtin.enter_worktree.find_git_root",
            new_callable=AsyncMock,
            side_effect=ToolInputError("not in a git repository"),
        ):
            tool = EnterWorktreeTool()
            with pytest.raises(ToolInputError, match="not in a git"):
                await _collect(tool, {"slug": "feat"}, ctx)

    @pytest.mark.asyncio
    async def test_git_unavailable_without_hook_surfaces_cc_error(self) -> None:
        """With no git and no WorktreeCreate hook registered, EnterWorktree
        surfaces CC's exact error message so the LLM can relay it."""
        ctx = _make_ctx(git_available=False)
        # No fire_hook on ctx — simulates "no hook subsystem available".
        tool = EnterWorktreeTool()
        with pytest.raises(ToolInputError, match="not in a git repository"):
            await _collect(tool, {"slug": "feat"}, ctx)

    @pytest.mark.asyncio
    async def test_sparse_paths(self, tmp_path: Path) -> None:
        wt_path = tmp_path / ".mustang" / "worktrees" / "feat"
        ctx = _make_ctx(cwd=tmp_path)

        with (
            patch(
                "kernel.tools.builtin.enter_worktree.find_git_root",
                new_callable=AsyncMock,
                return_value=tmp_path,
            ),
            patch(
                "kernel.tools.builtin.enter_worktree.create_worktree",
                new_callable=AsyncMock,
                return_value=(wt_path, "worktree-feat"),
            ),
            patch(
                "kernel.tools.builtin.enter_worktree.setup_sparse_checkout",
                new_callable=AsyncMock,
            ) as mock_sparse,
        ):
            tool = EnterWorktreeTool()
            results = await _collect(
                tool, {"slug": "feat", "sparse_paths": ["src/", "tests/"]}, ctx
            )

        mock_sparse.assert_awaited_once()
        assert results[0].data["sparse_paths"] == ["src/", "tests/"]

    @pytest.mark.asyncio
    async def test_context_modifier_sets_cwd(self, tmp_path: Path) -> None:

        wt_path = tmp_path / ".mustang" / "worktrees" / "feat"
        ctx = _make_ctx(cwd=tmp_path)

        with (
            patch(
                "kernel.tools.builtin.enter_worktree.find_git_root",
                new_callable=AsyncMock,
                return_value=tmp_path,
            ),
            patch(
                "kernel.tools.builtin.enter_worktree.create_worktree",
                new_callable=AsyncMock,
                return_value=(wt_path, "worktree-feat"),
            ),
        ):
            tool = EnterWorktreeTool()
            results = await _collect(tool, {"slug": "feat"}, ctx)

        modifier = results[0].context_modifier
        # Build a minimal ToolContext-like object to test the modifier.
        from kernel.tools.context import ToolContext
        from kernel.tools.file_state import FileStateCache

        old_ctx = ToolContext(
            session_id="s1",
            agent_depth=0,
            agent_id=None,
            cwd=tmp_path,
            cancel_event=asyncio.Event(),
            file_state=FileStateCache(),
        )
        new_ctx = modifier(old_ctx)
        assert new_ctx.cwd == wt_path
