"""Integration tests for GitManager + worktree tools + context pipeline."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kernel.git import GitManager
from kernel.git.types import GitConfig, GitContext


def _make_module_table(
    state_dir: Path,
    *,
    with_tool_manager: bool = True,
) -> MagicMock:
    mt = MagicMock()
    mt.state_dir = state_dir

    section_mock = MagicMock()
    section_mock.get.return_value = GitConfig(binary=None)
    section_mock.changed = MagicMock()
    section_mock.changed.connect = MagicMock(return_value=lambda: None)
    mt.config.bind_section.return_value = section_mock
    mt.config.get_section.return_value = None

    if with_tool_manager:
        tm = MagicMock()
        tm._registry = MagicMock()
        mt.get.return_value = tm
    else:
        mt.get.side_effect = KeyError("not loaded")

    return mt


class TestEnterExitRoundTrip:
    """Test the full enter → exit cycle at the GitManager level."""

    @pytest.mark.asyncio
    async def test_register_then_unregister(self, tmp_path: Path) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()

        from datetime import datetime, timezone

        from kernel.git.types import WorktreeSession

        ws = WorktreeSession(
            session_id="s1",
            original_cwd=tmp_path,
            worktree_path=tmp_path / ".mustang" / "worktrees" / "feat",
            worktree_branch="worktree-feat",
            slug="feat",
            created_at=datetime.now(timezone.utc),
        )
        await mgr.register_worktree(ws)
        assert mgr.get_worktree("s1") is ws

        removed = await mgr.unregister_worktree("s1")
        assert removed is ws
        assert mgr.get_worktree("s1") is None

        # DB should also be clean.
        assert await mgr._store.get_by_session("s1") is None

        await mgr.shutdown()


class TestContextRefreshAfterEnter:
    @pytest.mark.asyncio
    async def test_invalidate_forces_recompute(self, tmp_path: Path) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()

        ctx1 = GitContext("main", "main", "X", "", "")
        ctx2 = GitContext("feature", "main", "X", "M file", "abc fix")

        call_count = 0

        async def _build(git_mgr, cwd):
            nonlocal call_count
            call_count += 1
            return ctx1 if call_count == 1 else ctx2

        with patch("kernel.git.build_git_context", side_effect=_build):
            r1 = await mgr.get_context(Path("/repo"), "s1")
            assert r1.branch == "main"

            mgr.invalidate_context("s1")
            r2 = await mgr.get_context(Path("/repo"), "s1")
            assert r2.branch == "feature"

        await mgr.shutdown()


class TestFlagDisabled:
    @pytest.mark.asyncio
    async def test_tools_still_register_when_git_unavailable(
        self, tmp_path: Path
    ) -> None:
        """When git is unavailable, worktree tools still register so the
        WORKTREE_CREATE / WORKTREE_REMOVE hook-based fallback is
        reachable (CC parity)."""
        tm = MagicMock()
        tm.lookup = MagicMock(return_value=None)
        tm._registry = MagicMock()
        mt = _make_module_table(state_dir=tmp_path)
        mt.get.return_value = tm
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value=None):
            await mgr.startup()

        assert mgr.available is False
        # Tools register anyway — hook fallback is available to the LLM.
        assert mgr._tools_registered is True
        assert tm._registry.register.call_count == 2

        result = await mgr.get_context(Path("/repo"), "s1")
        assert result is None

        await mgr.shutdown()


class TestDeferredListingContainsTools:
    @pytest.mark.asyncio
    async def test_tools_registered_in_deferred_layer(
        self, tmp_path: Path
    ) -> None:
        tm = MagicMock()
        tm.lookup = MagicMock(return_value=None)
        tm._registry = MagicMock()
        mt = _make_module_table(state_dir=tmp_path)
        mt.get.return_value = tm

        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()

        # Two tools registered: EnterWorktree and ExitWorktree.
        assert tm._registry.register.call_count == 2
        call_args = [
            call.args[0].name for call in tm._registry.register.call_args_list
        ]
        assert "EnterWorktree" in call_args
        assert "ExitWorktree" in call_args

        # Both in deferred layer.
        for call in tm._registry.register.call_args_list:
            assert call.kwargs.get("layer") == "deferred"

        await mgr.shutdown()
