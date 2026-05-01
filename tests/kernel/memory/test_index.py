"""Tests for memory.index — in-memory cache and hotness computation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kernel.memory.index import MemoryIndex
from kernel.memory.store import ensure_directory_tree, write_memory
from kernel.memory.types import MemoryHeader


def _make_header(
    filename: str = "test",
    category: str = "semantic",
    source: str = "agent",
    access_count: int = 0,
    age_days: int = 0,
) -> MemoryHeader:
    now = datetime.now(timezone.utc)
    return MemoryHeader(
        filename=filename,
        name=filename,
        description=f"description of {filename}",
        category=category,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        created=now - timedelta(days=age_days),
        updated=now - timedelta(days=age_days),
        access_count=access_count,
        locked=False,
        rel_path=f"{category}/{filename}.md",
    )


@pytest.fixture()
def mem_root(tmp_path: Path) -> Path:
    root = tmp_path / "memory"
    ensure_directory_tree(root)
    return root


class TestHotnessComputation:
    def test_cold_start_not_zero(self) -> None:
        """access_count=0 should NOT produce hotness=0 (cold-start fix)."""
        h = _make_header(access_count=0, category="semantic")
        score = MemoryIndex.compute_hotness(h)
        assert score > 0, f"New memory hotness should be > 0, got {score}"

    def test_higher_access_higher_hotness(self) -> None:
        low = _make_header(access_count=1)
        high = _make_header(access_count=20)
        assert MemoryIndex.compute_hotness(high) > MemoryIndex.compute_hotness(low)

    def test_evergreen_no_decay(self) -> None:
        """Profile/semantic/procedural should not decay with age."""
        young = _make_header(category="profile", age_days=0)
        old = _make_header(category="profile", age_days=365)
        # Evergreen: same access_count → same hotness regardless of age
        assert MemoryIndex.compute_hotness(young) == pytest.approx(
            MemoryIndex.compute_hotness(old), rel=0.01
        )

    def test_episodic_decays(self) -> None:
        """Episodic memories should decay with age."""
        young = _make_header(category="episodic", age_days=0)
        old = _make_header(category="episodic", age_days=60)
        assert MemoryIndex.compute_hotness(young) > MemoryIndex.compute_hotness(old)

    def test_30_day_halflife(self) -> None:
        """After 30 days, episodic hotness should be ~half."""
        fresh = _make_header(category="episodic", age_days=0)
        aged = _make_header(category="episodic", age_days=30)
        ratio = MemoryIndex.compute_hotness(aged) / MemoryIndex.compute_hotness(fresh)
        assert ratio == pytest.approx(0.5, abs=0.05)

    def test_user_source_higher_than_extracted(self) -> None:
        user = _make_header(source="user")
        extracted = _make_header(source="extracted")
        assert MemoryIndex.compute_hotness(user) > MemoryIndex.compute_hotness(extracted)

    def test_classify_new_user_memory_is_warm_or_hot(self) -> None:
        """A brand-new user-created memory should NOT be cold."""
        h = _make_header(source="user", access_count=0, category="semantic")
        hotness = MemoryIndex.classify(h)
        assert hotness in ("warm", "hot"), f"New user memory classified as {hotness}"


class TestMemoryIndexLoad:
    @pytest.mark.anyio()
    async def test_load_empty(self, mem_root: Path) -> None:
        index = MemoryIndex()
        await index.load(mem_root)
        assert index.get_all_headers() == []
        assert index.get_index_text() == ""

    @pytest.mark.anyio()
    async def test_load_with_files(self, mem_root: Path) -> None:
        write_memory(mem_root, "profile", _make_header("identity", "profile"), "body")
        write_memory(mem_root, "semantic", _make_header("stack", "semantic"), "body")

        index = MemoryIndex()
        await index.load(mem_root)
        headers = index.get_all_headers()
        assert len(headers) == 2

    @pytest.mark.anyio()
    async def test_index_text_grouped(self, mem_root: Path) -> None:
        write_memory(mem_root, "profile", _make_header("identity", "profile"), "body")
        write_memory(mem_root, "semantic", _make_header("stack", "semantic"), "body")

        index = MemoryIndex()
        await index.load(mem_root)
        text = index.get_index_text()
        assert "## profile" in text
        assert "## semantic" in text

    @pytest.mark.anyio()
    async def test_invalidate_and_refresh(self, mem_root: Path) -> None:
        index = MemoryIndex()
        await index.load(mem_root)
        assert len(index.get_all_headers()) == 0

        # Write a new file
        write_memory(mem_root, "semantic", _make_header("new-mem", "semantic"), "body")
        # Still 0 (cached)
        assert len(index.get_all_headers()) == 0

        # Invalidate → should pick up new file
        index.invalidate()
        assert len(index.get_all_headers()) == 1

    @pytest.mark.anyio()
    async def test_get_header_by_name(self, mem_root: Path) -> None:
        write_memory(mem_root, "profile", _make_header("identity", "profile"), "body")
        index = MemoryIndex()
        await index.load(mem_root)
        h = index.get_header("identity")
        assert h is not None
        assert h.category == "profile"

    @pytest.mark.anyio()
    async def test_get_header_missing(self, mem_root: Path) -> None:
        index = MemoryIndex()
        await index.load(mem_root)
        assert index.get_header("nonexistent") is None

    @pytest.mark.anyio()
    async def test_get_headers_by_category(self, mem_root: Path) -> None:
        write_memory(mem_root, "profile", _make_header("id", "profile"), "body")
        write_memory(mem_root, "semantic", _make_header("s1", "semantic"), "body")
        write_memory(mem_root, "semantic", _make_header("s2", "semantic"), "body")

        index = MemoryIndex()
        await index.load(mem_root)
        sem = index.get_headers_by_category("semantic")
        assert len(sem) == 2
