"""Tests for kernel.git.GitManager — lifecycle, config signal, tool sync."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kernel.git import GitManager
from kernel.git.types import GitConfig, GitContext, WorktreeSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_module_table(
    *,
    state_dir: Path,
    tool_manager: MagicMock | None = None,
    has_tool_manager: bool = True,
) -> MagicMock:
    """Build a minimal mock KernelModuleTable."""
    mt = MagicMock()
    mt.state_dir = state_dir

    # ConfigManager mock — bind_section returns a MutableSection-like object
    section_mock = MagicMock()
    section_mock.get.return_value = GitConfig(binary=None)
    section_mock.changed = MagicMock()
    section_mock.changed.connect = MagicMock(return_value=lambda: None)
    mt.config.bind_section.return_value = section_mock
    mt.config.get_section.return_value = None  # no pre-existing section

    # ToolManager mock
    if has_tool_manager and tool_manager is not None:
        mt.get.return_value = tool_manager
    elif has_tool_manager:
        tm = MagicMock()
        tm._registry = MagicMock()
        mt.get.return_value = tm
    else:
        mt.get.side_effect = KeyError("ToolManager not loaded")

    return mt


def _make_ws(session_id: str = "s1", slug: str = "feat") -> WorktreeSession:
    from datetime import datetime, timezone

    return WorktreeSession(
        session_id=session_id,
        original_cwd=Path("/repo"),
        worktree_path=Path(f"/repo/.mustang/worktrees/{slug}"),
        worktree_branch=f"worktree-{slug}",
        slug=slug,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


class TestStartup:
    @pytest.mark.asyncio
    async def test_startup_git_available(self, tmp_path: Path) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()
        assert mgr.available is True
        assert mgr._git_bin == "/usr/bin/git"

    @pytest.mark.asyncio
    async def test_startup_git_not_found(self, tmp_path: Path) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value=None):
            await mgr.startup()
        # Must NOT raise — available is False instead.
        assert mgr.available is False
        assert mgr._git_bin is None

    @pytest.mark.asyncio
    async def test_startup_never_raises(self, tmp_path: Path) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        mt.config.bind_section.side_effect = RuntimeError("config boom")
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value=None):
            await mgr.startup()  # must not raise
        assert mgr.available is False


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


class TestResolveBinary:
    @pytest.mark.asyncio
    async def test_user_config_priority(self, tmp_path: Path) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        # Simulate user config: git.binary = "/opt/git"
        section = MagicMock()
        section.get.return_value = GitConfig(binary="/opt/git")
        mt.config.get_section.return_value = section

        mgr = GitManager(mt)

        def _which(name: str) -> str | None:
            if name == "/opt/git":
                return "/opt/git"
            return "/usr/bin/git"  # system fallback

        with patch.object(shutil, "which", side_effect=_which):
            await mgr.startup()
        assert mgr._git_bin == "/opt/git"

    @pytest.mark.asyncio
    async def test_system_path_fallback(self, tmp_path: Path) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()
        assert mgr._git_bin == "/usr/bin/git"


# ---------------------------------------------------------------------------
# Tool sync
# ---------------------------------------------------------------------------


class TestSyncTools:
    """Worktree tools always register — the git path is selected at
    call-time when ``git_manager.available`` is True, otherwise the
    tool falls back to WORKTREE_CREATE / WORKTREE_REMOVE hooks (CC
    parity).  ``_tools_registered`` is True after first startup."""

    @pytest.mark.asyncio
    async def test_registers_when_available(self, tmp_path: Path) -> None:
        tm = MagicMock()
        tm.lookup = MagicMock(return_value=None)  # force register path
        tm._registry = MagicMock()
        mt = _make_module_table(state_dir=tmp_path, tool_manager=tm)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()
        assert mgr._tools_registered is True
        assert tm._registry.register.call_count == 2  # Enter + Exit

    @pytest.mark.asyncio
    async def test_registers_even_when_git_unavailable(
        self, tmp_path: Path
    ) -> None:
        """Hook-fallback path: tools must be visible to the LLM even
        when git is missing, so a WorktreeCreate hook can service the
        call."""
        tm = MagicMock()
        tm.lookup = MagicMock(return_value=None)
        tm._registry = MagicMock()
        mt = _make_module_table(state_dir=tmp_path, tool_manager=tm)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value=None):
            await mgr.startup()
        assert mgr._tools_registered is True
        assert tm._registry.register.call_count == 2

    @pytest.mark.asyncio
    async def test_config_change_keeps_tools_registered(
        self, tmp_path: Path
    ) -> None:
        """Git coming and going only flips the availability flag — the
        tools stay registered so the LLM's schema cache stays stable."""
        tm = MagicMock()
        tm.lookup = MagicMock(return_value=None)
        tm._registry = MagicMock()
        mt = _make_module_table(state_dir=tmp_path, tool_manager=tm)
        mgr = GitManager(mt)

        # Start with git available — tools register once.
        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()
        assert mgr._tools_registered is True
        initial_register_count = tm._registry.register.call_count

        # Simulate config change: git goes away.  Tools stay registered,
        # no unregister call, only availability flag flips.
        with patch.object(shutil, "which", return_value=None):
            mt.config.get_section.return_value = None
            await mgr._on_config_changed(
                GitConfig(binary=None), GitConfig(binary=None)
            )
        assert mgr._tools_registered is True
        assert tm._registry.register.call_count == initial_register_count
        tm._registry.unregister.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_tool_manager_is_safe(self, tmp_path: Path) -> None:
        mt = _make_module_table(
            state_dir=tmp_path, has_tool_manager=False
        )
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()  # should not raise
        assert mgr._tools_registered is False


# ---------------------------------------------------------------------------
# Git context cache
# ---------------------------------------------------------------------------


class TestContextCache:
    @pytest.mark.asyncio
    async def test_get_context_caches(self, tmp_path: Path) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()

        ctx = GitContext("main", "main", "X", "", "")
        with patch(
            "kernel.git.build_git_context", new_callable=AsyncMock
        ) as mock_build:
            mock_build.return_value = ctx
            r1 = await mgr.get_context(Path("/repo"), "s1")
            r2 = await mgr.get_context(Path("/repo"), "s1")
        assert r1 is ctx
        assert r2 is ctx
        mock_build.assert_called_once()  # cached, not called twice

    @pytest.mark.asyncio
    async def test_invalidate_context(self, tmp_path: Path) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()

        ctx = GitContext("main", "main", "X", "", "")
        with patch(
            "kernel.git.build_git_context", new_callable=AsyncMock
        ) as mock_build:
            mock_build.return_value = ctx
            await mgr.get_context(Path("/repo"), "s1")
            mgr.invalidate_context("s1")
            await mgr.get_context(Path("/repo"), "s1")
        assert mock_build.call_count == 2  # called again after invalidate

    @pytest.mark.asyncio
    async def test_get_context_unavailable(self, tmp_path: Path) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value=None):
            await mgr.startup()
        result = await mgr.get_context(Path("/repo"), "s1")
        assert result is None


# ---------------------------------------------------------------------------
# Worktree register / unregister
# ---------------------------------------------------------------------------


class TestWorktreeTracking:
    @pytest.mark.asyncio
    async def test_register_and_get(self, tmp_path: Path) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()

        ws = _make_ws()
        await mgr.register_worktree(ws)
        assert mgr.get_worktree("s1") is ws

        # Also persisted in store.
        db_ws = await mgr._store.get_by_session("s1")
        assert db_ws is not None
        assert db_ws.slug == "feat"

    @pytest.mark.asyncio
    async def test_unregister(self, tmp_path: Path) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()

        ws = _make_ws()
        await mgr.register_worktree(ws)
        removed = await mgr.unregister_worktree("s1")
        assert removed is ws
        assert mgr.get_worktree("s1") is None

        # Also removed from store.
        db_ws = await mgr._store.get_by_session("s1")
        assert db_ws is None


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_cleans_no_change_worktrees(
        self, tmp_path: Path
    ) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()

        ws = _make_ws()
        await mgr.register_worktree(ws)

        with (
            patch(
                "kernel.git.count_changes", new_callable=AsyncMock
            ) as mock_count,
            patch(
                "kernel.git.remove_worktree", new_callable=AsyncMock
            ) as mock_remove,
        ):
            mock_count.return_value = 0
            await mgr.shutdown()

        mock_remove.assert_called_once()
        assert mgr.get_worktree("s1") is None

    @pytest.mark.asyncio
    async def test_shutdown_keeps_changed_worktrees(
        self, tmp_path: Path
    ) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()

        ws = _make_ws()
        await mgr.register_worktree(ws)

        with (
            patch(
                "kernel.git.count_changes", new_callable=AsyncMock
            ) as mock_count,
            patch(
                "kernel.git.remove_worktree", new_callable=AsyncMock
            ) as mock_remove,
        ):
            mock_count.return_value = 3
            await mgr.shutdown()

        mock_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_unavailable_skips_cleanup(
        self, tmp_path: Path
    ) -> None:
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)
        with patch.object(shutil, "which", return_value=None):
            await mgr.startup()

        # Manually add a worktree (simulating weird state).
        mgr._worktrees["s1"] = _make_ws()
        await mgr.shutdown()  # should not try git commands
