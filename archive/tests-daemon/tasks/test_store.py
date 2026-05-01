"""Tests for TaskStore — session-scoped task persistence."""

from __future__ import annotations

import json
from pathlib import Path

from daemon.tasks.store import TaskItem, TaskStore


class TestRoundTrip:
    """Save + load basics."""

    def test_load_missing_file(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path, "sess-a")
        assert store.load() == []

    def test_save_and_load(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path, "sess-a")
        items = [
            TaskItem(content="Run tests", status="pending", active_form="Running tests"),
            TaskItem(
                content="Write docs",
                status="in_progress",
                active_form="Writing docs",
            ),
        ]
        store.save(items)
        loaded = store.load()
        assert [t.content for t in loaded] == ["Run tests", "Write docs"]
        assert loaded[1].status == "in_progress"

    def test_save_overwrites(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path, "sess-a")
        store.save([TaskItem(content="a", status="pending", active_form="A")])
        store.save([TaskItem(content="b", status="completed", active_form="B")])
        loaded = store.load()
        assert len(loaded) == 1
        assert loaded[0].content == "b"

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested" / "dir"
        store = TaskStore(nested, "sess-a")
        store.save([TaskItem(content="x", status="pending", active_form="X")])
        assert nested.exists()


class TestCorruption:
    """Malformed files do not crash the store."""

    def test_load_corrupt_file(self, tmp_path: Path) -> None:
        path = tmp_path / "sess-a.tasks.json"
        path.write_text("not json", encoding="utf-8")
        store = TaskStore(tmp_path, "sess-a")
        assert store.load() == []

    def test_load_invalid_schema(self, tmp_path: Path) -> None:
        path = tmp_path / "sess-a.tasks.json"
        path.write_text(json.dumps({"tasks": "bad"}), encoding="utf-8")
        store = TaskStore(tmp_path, "sess-a")
        assert store.load() == []


class TestClear:
    def test_clear_removes_file(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path, "sess-a")
        store.save([TaskItem(content="x", status="pending", active_form="X")])
        assert store.path.exists()
        store.clear()
        assert not store.path.exists()

    def test_clear_missing_is_noop(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path, "sess-a")
        store.clear()
        assert not store.path.exists()
