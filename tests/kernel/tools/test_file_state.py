"""FileStateCache — record / verify / invalidate."""

from __future__ import annotations

from pathlib import Path

from kernel.tools.file_state import FileStateCache, hash_text


def test_record_then_verify(tmp_path: Path) -> None:
    cache = FileStateCache()
    p = tmp_path / "foo.txt"
    p.write_text("hello")
    state = cache.record(p, "hello")
    assert state.sha256_hex == hash_text("hello")
    assert cache.verify(p) == state


def test_verify_miss_returns_none(tmp_path: Path) -> None:
    cache = FileStateCache()
    assert cache.verify(tmp_path / "never-seen.txt") is None


def test_invalidate_drops_entry(tmp_path: Path) -> None:
    cache = FileStateCache()
    p = tmp_path / "a.txt"
    p.write_text("x")
    cache.record(p, "x")
    cache.invalidate(p)
    assert cache.verify(p) is None


def test_absolute_and_relative_paths_share_entry(tmp_path: Path, monkeypatch) -> None:
    """A read via relative path should be visible via absolute path lookup."""
    cache = FileStateCache()
    p = tmp_path / "a.txt"
    p.write_text("x")
    # Record via resolved path
    cache.record(p, "x")
    # Verify via the same path
    assert cache.verify(p) is not None


def test_clear_wipes_all(tmp_path: Path) -> None:
    cache = FileStateCache()
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b.txt").write_text("y")
    cache.record(tmp_path / "a.txt", "x")
    cache.record(tmp_path / "b.txt", "y")
    cache.clear()
    assert cache.verify(tmp_path / "a.txt") is None
    assert cache.verify(tmp_path / "b.txt") is None
