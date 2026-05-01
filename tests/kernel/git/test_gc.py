"""Tests for GitManager startup GC — crash-recovery worktree cleanup."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kernel.git import GitManager
from kernel.git.types import WorktreeSession


def _make_module_table(state_dir: Path) -> MagicMock:
    mt = MagicMock()
    mt.state_dir = state_dir
    section_mock = MagicMock()
    section_mock.changed = MagicMock()
    section_mock.changed.connect = MagicMock(return_value=lambda: None)
    mt.config.bind_section.return_value = section_mock
    mt.config.get_section.return_value = None
    # No ToolManager — GC tests don't need tool sync.
    mt.get.side_effect = KeyError("not loaded")
    return mt


def _make_ws(
    tmp_path: Path,
    session_id: str = "s1",
    slug: str = "feat",
    create_dir: bool = True,
) -> WorktreeSession:
    from datetime import datetime, timezone

    wt_path = tmp_path / ".mustang" / "worktrees" / slug
    if create_dir:
        wt_path.mkdir(parents=True, exist_ok=True)
        (wt_path / ".git").write_text("gitdir: ../../../.git/worktrees/feat")
    return WorktreeSession(
        session_id=session_id,
        original_cwd=tmp_path,
        worktree_path=wt_path,
        worktree_branch=f"worktree-{slug}",
        slug=slug,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class TestStartupGC:
    @pytest.mark.asyncio
    async def test_gc_dir_gone_deletes_record(self, tmp_path: Path) -> None:
        """DB has record but dir was manually deleted → clean DB only."""
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)

        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()

        # Insert a stale record pointing to a non-existent dir.
        ws = _make_ws(tmp_path, create_dir=False)
        await mgr._store.insert(ws)

        # Re-run GC.
        await mgr._gc_stale_worktrees()

        # DB record should be cleaned.
        result = await mgr._store.get_by_session("s1")
        assert result is None

    @pytest.mark.asyncio
    async def test_gc_no_changes_removes(self, tmp_path: Path) -> None:
        """DB + dir exists + no changes → remove worktree + delete record."""
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)

        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()

        ws = _make_ws(tmp_path, create_dir=True)
        await mgr._store.insert(ws)

        with (
            patch(
                "kernel.git.count_changes", new_callable=AsyncMock
            ) as mock_count,
            patch(
                "kernel.git.remove_worktree", new_callable=AsyncMock
            ) as mock_remove,
        ):
            mock_count.return_value = 0
            await mgr._gc_stale_worktrees()

        mock_remove.assert_called_once()
        result = await mgr._store.get_by_session("s1")
        assert result is None

    @pytest.mark.asyncio
    async def test_gc_has_changes_keeps(self, tmp_path: Path) -> None:
        """DB + dir exists + has changes → keep both."""
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)

        with patch.object(shutil, "which", return_value="/usr/bin/git"):
            await mgr.startup()

        ws = _make_ws(tmp_path, create_dir=True)
        await mgr._store.insert(ws)

        with patch(
            "kernel.git.count_changes", new_callable=AsyncMock
        ) as mock_count:
            mock_count.return_value = 5
            await mgr._gc_stale_worktrees()

        # Record should still be there.
        result = await mgr._store.get_by_session("s1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_gc_unavailable_skips(self, tmp_path: Path) -> None:
        """Git unavailable → GC does nothing."""
        mt = _make_module_table(state_dir=tmp_path)
        mgr = GitManager(mt)

        with patch.object(shutil, "which", return_value=None):
            await mgr.startup()
        assert mgr.available is False

        # Manually insert a record.
        await mgr._store.insert(_make_ws(tmp_path, create_dir=True))

        # GC should be a no-op (no git to run commands with).
        await mgr._gc_stale_worktrees()

        # Record is still there (GC skipped).
        result = await mgr._store.get_by_session("s1")
        assert result is not None
