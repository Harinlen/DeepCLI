"""Tests for file state cache — stale-write prevention.

Covers:
- FileStateCache record/check/update/clone operations
- Integration with FileReadTool, FileEditTool, FileWriteTool
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from daemon.extensions.tools.base import ToolContext
from daemon.extensions.tools.file_state_cache import FileStateCache


# ------------------------------------------------------------------
# FileStateCache unit tests
# ------------------------------------------------------------------


class TestFileStateCacheRecord:
    """Tests for recording file state."""

    def test_record_read(self, tmp_path: Path) -> None:
        """Record a file read and verify it's in the cache."""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        cache = FileStateCache()
        cache.record_read(str(f))
        assert str(f) in cache
        assert len(cache) == 1

    def test_record_read_nonexistent(self) -> None:
        """Recording a non-existent file is a silent no-op."""
        cache = FileStateCache()
        cache.record_read("/nonexistent/path.txt")
        assert len(cache) == 0

    def test_record_read_partial(self, tmp_path: Path) -> None:
        """Partial reads are recorded with is_partial=True."""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        cache = FileStateCache()
        cache.record_read_partial(str(f))
        assert str(f) in cache

    def test_lru_eviction(self, tmp_path: Path) -> None:
        """Cache evicts oldest entries when full."""
        cache = FileStateCache(max_entries=3)
        for i in range(5):
            f = tmp_path / f"file{i}.txt"
            f.write_text(f"content {i}")
            cache.record_read(str(f))
        assert len(cache) == 3
        # First two should be evicted
        assert str(tmp_path / "file0.txt") not in cache
        assert str(tmp_path / "file1.txt") not in cache
        assert str(tmp_path / "file4.txt") in cache


class TestFileStateCacheCheck:
    """Tests for check_before_write validation."""

    def test_read_then_edit_same_content(self, tmp_path: Path) -> None:
        """Read → edit with no external changes → ok."""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        cache = FileStateCache()
        cache.record_read(str(f))
        ok, msg = cache.check_before_write(str(f))
        assert ok is True

    def test_read_then_external_modify_then_edit(self, tmp_path: Path) -> None:
        """Read → external modify → edit → error."""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        cache = FileStateCache()
        cache.record_read(str(f))

        # External modification
        time.sleep(0.05)  # Ensure mtime differs
        f.write_text("modified by external tool")

        ok, msg = cache.check_before_write(str(f))
        assert ok is False
        assert "modified externally" in msg.lower()

    def test_no_prior_read_existing_file(self, tmp_path: Path) -> None:
        """Edit without prior read on existing file → error."""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        cache = FileStateCache()
        ok, msg = cache.check_before_write(str(f))
        assert ok is False
        assert "read the file" in msg.lower()

    def test_no_prior_read_new_file(self, tmp_path: Path) -> None:
        """Write to a new file (doesn't exist) → ok."""
        f = tmp_path / "new_file.txt"
        cache = FileStateCache()
        ok, msg = cache.check_before_write(str(f))
        assert ok is True

    def test_mtime_changed_but_content_same(self, tmp_path: Path) -> None:
        """Mtime differs but content hash matches → ok (e.g. git touch)."""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        cache = FileStateCache()
        cache.record_read(str(f))

        # Touch the file (change mtime, same content)
        time.sleep(0.05)
        os.utime(str(f), None)

        ok, msg = cache.check_before_write(str(f))
        assert ok is True

    def test_file_deleted_between_read_and_write(self, tmp_path: Path) -> None:
        """File deleted after read → error."""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        cache = FileStateCache()
        cache.record_read(str(f))
        f.unlink()
        ok, msg = cache.check_before_write(str(f))
        assert ok is False
        assert "no longer exists" in msg.lower()


class TestFileStateCacheUpdate:
    """Tests for update_after_write."""

    def test_update_allows_subsequent_edit(self, tmp_path: Path) -> None:
        """Read → edit → update → edit again → ok."""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        cache = FileStateCache()
        cache.record_read(str(f))

        # Simulate edit
        f.write_text("modified")
        cache.update_after_write(str(f))

        # Second check should pass
        ok, msg = cache.check_before_write(str(f))
        assert ok is True


class TestFileStateCacheClone:
    """Tests for cache cloning (sub-agent isolation)."""

    def test_clone_inherits_state(self, tmp_path: Path) -> None:
        """Cloned cache contains parent's entries."""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        parent = FileStateCache()
        parent.record_read(str(f))

        child = parent.clone()
        assert str(f) in child
        ok, msg = child.check_before_write(str(f))
        assert ok is True

    def test_clone_is_independent(self, tmp_path: Path) -> None:
        """Mutations on child don't affect parent."""
        f = tmp_path / "test.txt"
        f2 = tmp_path / "test2.txt"
        f.write_text("hello")
        f2.write_text("world")

        parent = FileStateCache()
        parent.record_read(str(f))

        child = parent.clone()
        child.record_read(str(f2))

        assert str(f2) in child
        assert str(f2) not in parent


# ------------------------------------------------------------------
# Integration with file tools
# ------------------------------------------------------------------


def _make_ctx(tmp_path: Path, cache: FileStateCache | None = None) -> ToolContext:
    """Create a ToolContext with a file state cache."""
    return ToolContext(
        cwd=str(tmp_path),
        file_state_cache=cache,
    )


class TestFileReadToolIntegration:
    """FileReadTool records state after reading."""

    @pytest.mark.asyncio
    async def test_records_state_after_read(self, tmp_path: Path) -> None:
        from daemon.extensions.tools.builtin.file_read import FileReadTool

        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3")
        cache = FileStateCache()
        ctx = _make_ctx(tmp_path, cache)
        tool = FileReadTool()
        result = await tool.execute({"file_path": str(f)}, ctx)
        assert not result.is_error
        assert str(f) in cache

    @pytest.mark.asyncio
    async def test_partial_read_records_state(self, tmp_path: Path) -> None:
        from daemon.extensions.tools.builtin.file_read import FileReadTool

        f = tmp_path / "test.txt"
        f.write_text("\n".join(f"line {i}" for i in range(100)))
        cache = FileStateCache()
        ctx = _make_ctx(tmp_path, cache)
        tool = FileReadTool()
        result = await tool.execute({"file_path": str(f), "offset": 0, "limit": 10}, ctx)
        assert not result.is_error
        assert str(f) in cache


class TestFileEditToolIntegration:
    """FileEditTool checks and updates state."""

    @pytest.mark.asyncio
    async def test_edit_after_read_succeeds(self, tmp_path: Path) -> None:
        from daemon.extensions.tools.builtin.file_edit import FileEditTool
        from daemon.extensions.tools.builtin.file_read import FileReadTool

        f = tmp_path / "test.txt"
        f.write_text("hello world")
        cache = FileStateCache()
        ctx = _make_ctx(tmp_path, cache)

        # Read first
        read_tool = FileReadTool()
        await read_tool.execute({"file_path": str(f)}, ctx)

        # Edit should succeed
        edit_tool = FileEditTool()
        result = await edit_tool.execute(
            {"file_path": str(f), "old_string": "hello", "new_string": "goodbye"},
            ctx,
        )
        assert not result.is_error
        assert f.read_text() == "goodbye world"

    @pytest.mark.asyncio
    async def test_edit_without_read_fails(self, tmp_path: Path) -> None:
        from daemon.extensions.tools.builtin.file_edit import FileEditTool

        f = tmp_path / "test.txt"
        f.write_text("hello world")
        cache = FileStateCache()
        ctx = _make_ctx(tmp_path, cache)

        edit_tool = FileEditTool()
        result = await edit_tool.execute(
            {"file_path": str(f), "old_string": "hello", "new_string": "goodbye"},
            ctx,
        )
        assert result.is_error
        assert "read the file" in result.output.lower()

    @pytest.mark.asyncio
    async def test_edit_after_external_modify_fails(self, tmp_path: Path) -> None:
        from daemon.extensions.tools.builtin.file_edit import FileEditTool
        from daemon.extensions.tools.builtin.file_read import FileReadTool

        f = tmp_path / "test.txt"
        f.write_text("hello world")
        cache = FileStateCache()
        ctx = _make_ctx(tmp_path, cache)

        # Read
        read_tool = FileReadTool()
        await read_tool.execute({"file_path": str(f)}, ctx)

        # External modification
        time.sleep(0.05)
        f.write_text("externally changed")

        # Edit should fail
        edit_tool = FileEditTool()
        result = await edit_tool.execute(
            {"file_path": str(f), "old_string": "externally", "new_string": "locally"},
            ctx,
        )
        assert result.is_error
        assert "modified externally" in result.output.lower()

    @pytest.mark.asyncio
    async def test_edit_updates_cache_for_subsequent_edits(self, tmp_path: Path) -> None:
        from daemon.extensions.tools.builtin.file_edit import FileEditTool
        from daemon.extensions.tools.builtin.file_read import FileReadTool

        f = tmp_path / "test.txt"
        f.write_text("aaa bbb ccc")
        cache = FileStateCache()
        ctx = _make_ctx(tmp_path, cache)

        read_tool = FileReadTool()
        await read_tool.execute({"file_path": str(f)}, ctx)

        edit_tool = FileEditTool()
        # First edit
        result1 = await edit_tool.execute(
            {"file_path": str(f), "old_string": "aaa", "new_string": "xxx"},
            ctx,
        )
        assert not result1.is_error

        # Second edit (no re-read needed, cache updated by first edit)
        result2 = await edit_tool.execute(
            {"file_path": str(f), "old_string": "bbb", "new_string": "yyy"},
            ctx,
        )
        assert not result2.is_error
        assert f.read_text() == "xxx yyy ccc"


class TestFileWriteToolIntegration:
    """FileWriteTool checks and updates state."""

    @pytest.mark.asyncio
    async def test_write_new_file_succeeds(self, tmp_path: Path) -> None:
        from daemon.extensions.tools.builtin.file_write import FileWriteTool

        f = tmp_path / "new.txt"
        cache = FileStateCache()
        ctx = _make_ctx(tmp_path, cache)

        tool = FileWriteTool()
        result = await tool.execute({"file_path": str(f), "content": "new content"}, ctx)
        assert not result.is_error
        assert str(f) in cache

    @pytest.mark.asyncio
    async def test_overwrite_without_read_fails(self, tmp_path: Path) -> None:
        from daemon.extensions.tools.builtin.file_write import FileWriteTool

        f = tmp_path / "existing.txt"
        f.write_text("original")
        cache = FileStateCache()
        ctx = _make_ctx(tmp_path, cache)

        tool = FileWriteTool()
        result = await tool.execute({"file_path": str(f), "content": "overwritten"}, ctx)
        assert result.is_error
        assert "read the file" in result.output.lower()

    @pytest.mark.asyncio
    async def test_overwrite_after_read_succeeds(self, tmp_path: Path) -> None:
        from daemon.extensions.tools.builtin.file_read import FileReadTool
        from daemon.extensions.tools.builtin.file_write import FileWriteTool

        f = tmp_path / "existing.txt"
        f.write_text("original")
        cache = FileStateCache()
        ctx = _make_ctx(tmp_path, cache)

        # Read first
        read_tool = FileReadTool()
        await read_tool.execute({"file_path": str(f)}, ctx)

        # Write should succeed
        write_tool = FileWriteTool()
        result = await write_tool.execute({"file_path": str(f), "content": "overwritten"}, ctx)
        assert not result.is_error
        assert f.read_text() == "overwritten"


class TestNoCache:
    """Tools work normally when file_state_cache is None."""

    @pytest.mark.asyncio
    async def test_edit_without_cache(self, tmp_path: Path) -> None:
        from daemon.extensions.tools.builtin.file_edit import FileEditTool

        f = tmp_path / "test.txt"
        f.write_text("hello world")
        ctx = _make_ctx(tmp_path, cache=None)

        tool = FileEditTool()
        result = await tool.execute(
            {"file_path": str(f), "old_string": "hello", "new_string": "goodbye"},
            ctx,
        )
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_write_without_cache(self, tmp_path: Path) -> None:
        from daemon.extensions.tools.builtin.file_write import FileWriteTool

        f = tmp_path / "test.txt"
        f.write_text("original")
        ctx = _make_ctx(tmp_path, cache=None)

        tool = FileWriteTool()
        result = await tool.execute({"file_path": str(f), "content": "new"}, ctx)
        assert not result.is_error
