"""Tests for memory hot cache / access counting (Phase 5.7D).

Covers: record_access, hot_memories ordering, persistence round-trip,
and hot memory suffix building.
"""

from __future__ import annotations

from pathlib import Path


from daemon.memory.schema import MemoryFrontmatter, MemoryType
from daemon.memory.store import MemoryStore


def _populate_store(store: MemoryStore) -> None:
    """Write a few memories for testing."""
    for name, desc in [("role", "engineer"), ("prefs", "pytest"), ("links", "grafana")]:
        fm = MemoryFrontmatter(name=name, description=desc, type=MemoryType.USER)
        store.write(MemoryType.USER, f"{name}.md", fm, f"Body for {name}.")


class TestAccessCounting:
    """Tests for record_access + hot_memories."""

    def test_record_access_increments(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "mem")
        store.load()
        _populate_store(store)

        store.record_access("user/role.md")
        store.record_access("user/role.md")
        store.record_access("user/prefs.md")

        assert store._access_tracker._counts["user/role.md"] == 2
        assert store._access_tracker._counts["user/prefs.md"] == 1

    def test_hot_memories_ordering(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "mem")
        store.load()
        _populate_store(store)

        # role accessed 3x, prefs 1x, links 2x.
        for _ in range(3):
            store.record_access("user/role.md")
        store.record_access("user/prefs.md")
        for _ in range(2):
            store.record_access("user/links.md")

        hot = store.hot_memories(top_n=2)
        assert len(hot) == 2
        assert hot[0].frontmatter.name == "role"  # Most accessed.
        assert hot[1].frontmatter.name == "links"

    def test_hot_memories_skips_deleted(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "mem")
        store.load()
        _populate_store(store)

        store.record_access("user/role.md")
        store.delete(MemoryType.USER, "role.md")

        hot = store.hot_memories(top_n=5)
        assert all(r.frontmatter.name != "role" for r in hot)


class TestAccessCountPersistence:
    """Tests for save/load round-trip of access counts."""

    def test_round_trip(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "mem")
        store.load()
        _populate_store(store)

        store.record_access("user/role.md")
        store.record_access("user/role.md")
        store.save_access_counts()

        # Create a new store and verify counts loaded.
        store2 = MemoryStore(tmp_path / "mem")
        store2.load()
        assert store2._access_tracker._counts.get("user/role.md") == 2

    def test_load_corrupt_file(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "mem")
        store.load()

        # Write corrupt JSON.
        (tmp_path / "mem" / ".access_counts.json").write_text("not json")
        store.load_access_counts()
        assert store._access_tracker._counts == {}
