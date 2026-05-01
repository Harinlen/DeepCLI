"""Tests for kernel.git.store — WorktreeStore SQLite persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from kernel.git.store import WorktreeStore
from kernel.git.types import WorktreeSession


def _make_ws(
    session_id: str = "sess-1",
    slug: str = "feat",
) -> WorktreeSession:
    return WorktreeSession(
        session_id=session_id,
        original_cwd=Path("/home/user/repo"),
        worktree_path=Path(f"/home/user/repo/.mustang/worktrees/{slug}"),
        worktree_branch=f"worktree-{slug}",
        slug=slug,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
async def store(tmp_path: Path):
    db_path = tmp_path / "kernel.db"
    s = WorktreeStore(db_path)
    await s.open()
    yield s
    await s.close()


class TestWorktreeStore:
    @pytest.mark.asyncio
    async def test_insert_and_list(self, store: WorktreeStore) -> None:
        ws = _make_ws()
        await store.insert(ws)
        result = await store.list_all()
        assert len(result) == 1
        assert result[0].session_id == "sess-1"
        assert result[0].slug == "feat"
        assert result[0].worktree_branch == "worktree-feat"

    @pytest.mark.asyncio
    async def test_delete(self, store: WorktreeStore) -> None:
        ws = _make_ws()
        await store.insert(ws)
        await store.delete("sess-1")
        result = await store.list_all()
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, store: WorktreeStore) -> None:
        # Should not raise.
        await store.delete("no-such-session")

    @pytest.mark.asyncio
    async def test_get_by_session_found(self, store: WorktreeStore) -> None:
        ws = _make_ws()
        await store.insert(ws)
        result = await store.get_by_session("sess-1")
        assert result is not None
        assert result.slug == "feat"

    @pytest.mark.asyncio
    async def test_get_by_session_missing(self, store: WorktreeStore) -> None:
        result = await store.get_by_session("no-such")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_all_empty(self, store: WorktreeStore) -> None:
        result = await store.list_all()
        assert result == []

    @pytest.mark.asyncio
    async def test_insert_or_replace(self, store: WorktreeStore) -> None:
        ws1 = _make_ws(slug="feat-v1")
        await store.insert(ws1)
        ws2 = _make_ws(slug="feat-v2")  # same session_id, different slug
        await store.insert(ws2)
        result = await store.list_all()
        assert len(result) == 1
        assert result[0].slug == "feat-v2"

    @pytest.mark.asyncio
    async def test_idempotent_schema_creation(self, tmp_path: Path) -> None:
        db_path = tmp_path / "kernel.db"
        s1 = WorktreeStore(db_path)
        await s1.open()
        await s1.close()
        # Open again — CREATE TABLE IF NOT EXISTS should not fail.
        s2 = WorktreeStore(db_path)
        await s2.open()
        await s2.close()

    @pytest.mark.asyncio
    async def test_path_roundtrip(self, store: WorktreeStore) -> None:
        ws = _make_ws()
        await store.insert(ws)
        result = await store.get_by_session("sess-1")
        assert result is not None
        assert isinstance(result.worktree_path, Path)
        assert isinstance(result.original_cwd, Path)
        assert result.worktree_path == ws.worktree_path
        assert result.original_cwd == ws.original_cwd

    @pytest.mark.asyncio
    async def test_datetime_roundtrip(self, store: WorktreeStore) -> None:
        ws = _make_ws()
        await store.insert(ws)
        result = await store.get_by_session("sess-1")
        assert result is not None
        assert result.created_at == ws.created_at
