"""Tests for the tool result store and budget enforcement."""

from __future__ import annotations

import time
from pathlib import Path

from daemon.extensions.tools.result_store import (
    DEFAULT_MAX_RESULT_CHARS,
    ResultStore,
    _generate_preview,
)


class TestGeneratePreview:
    """Tests for the _generate_preview helper."""

    def test_short_content_unchanged(self) -> None:
        """Content under limit is returned as-is."""
        text = "short text"
        preview, has_more = _generate_preview(text, max_chars=100)
        assert preview == text
        assert has_more is False

    def test_truncated_at_newline(self) -> None:
        """Prefers cutting at a newline in the upper 50%."""
        # Build content: 600 chars of 'a', newline at 550, then more
        text = "a" * 550 + "\n" + "b" * 449
        preview, has_more = _generate_preview(text, max_chars=1000)
        assert preview == text  # Under 1000, no truncation
        assert has_more is False

        # Now with a 600-char limit — newline at 550 is in upper 50%
        preview, has_more = _generate_preview(text, max_chars=600)
        assert preview == "a" * 550
        assert has_more is True

    def test_truncated_at_exact_limit(self) -> None:
        """Falls back to exact limit when no good newline exists."""
        text = "x" * 2000  # No newlines at all
        preview, has_more = _generate_preview(text, max_chars=500)
        assert len(preview) == 500
        assert has_more is True

    def test_newline_in_lower_half_ignored(self) -> None:
        """Newlines in the lower 50% of the window are ignored."""
        # Newline at position 100 out of 1000 limit — in lower half
        text = "a" * 100 + "\n" + "b" * 2000
        preview, has_more = _generate_preview(text, max_chars=1000)
        # Should cut at 1000, not at 100
        assert len(preview) == 1000
        assert has_more is True


class TestResultStore:
    """Tests for ResultStore."""

    def test_store_writes_file(self, tmp_path: Path) -> None:
        """store() creates a file and returns summary."""
        store = ResultStore(tmp_path)
        content = "a" * 5000
        summary = store.store(content)

        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].suffix == ".txt"
        assert files[0].read_text() == content

        assert str(tmp_path) in summary
        assert "file_read" in summary
        assert "Output too large" in summary

    def test_store_dedup_by_hash(self, tmp_path: Path) -> None:
        """Identical content is not written twice."""
        store = ResultStore(tmp_path)
        content = "b" * 5000

        store.store(content)
        store.store(content)

        files = list(tmp_path.iterdir())
        assert len(files) == 1

    def test_store_different_content(self, tmp_path: Path) -> None:
        """Different content creates different files."""
        store = ResultStore(tmp_path)
        store.store("content_one_" + "x" * 5000)
        store.store("content_two_" + "y" * 5000)

        files = list(tmp_path.iterdir())
        assert len(files) == 2

    def test_cleanup_on_startup(self, tmp_path: Path) -> None:
        """cleanup_on_startup removes all files."""
        (tmp_path / "old1.txt").write_text("old data")
        (tmp_path / "old2.txt").write_text("old data")

        store = ResultStore(tmp_path)
        removed = store.cleanup_on_startup()

        assert removed == 2
        assert list(tmp_path.iterdir()) == []

    def test_cleanup_on_startup_missing_dir(self, tmp_path: Path) -> None:
        """cleanup_on_startup with nonexistent dir returns 0."""
        store = ResultStore(tmp_path / "nonexistent")
        assert store.cleanup_on_startup() == 0

    def test_eviction_on_size_cap(self, tmp_path: Path) -> None:
        """Files are evicted when directory exceeds max size."""
        store = ResultStore(tmp_path, max_cache_size=500)

        # Write 8 files (~800 bytes total > 500 cap)
        for i in range(8):
            content = f"file_{i}_" + "x" * 90
            path = tmp_path / f"{i:04d}.txt"
            path.write_text(content)
            time.sleep(0.01)

        store._evict_if_needed()

        remaining = list(tmp_path.iterdir())
        total_size = sum(f.stat().st_size for f in remaining)
        assert total_size <= 500

    def test_store_preview_uses_2000_chars(self, tmp_path: Path) -> None:
        """Preview in summary is limited to ~2000 chars."""
        store = ResultStore(tmp_path)
        content = "A" * 5000
        summary = store.store(content)

        # Preview should contain 2000 A's, not 5000
        assert "A" * 2000 in summary
        assert "A" * 2001 not in summary

    def test_store_preview_with_newline_boundary(self, tmp_path: Path) -> None:
        """Preview cuts at newline boundary when available."""
        store = ResultStore(tmp_path)
        # Newline at position 1800 (in upper 50% of 2000 limit)
        marker = "AFTER_NEWLINE_MARKER"
        content = "x" * 1800 + "\n" + marker + "z" * 5000
        summary = store.store(content, tool_name="test")

        # Should cut at the newline, not at 2000
        assert "x" * 1800 in summary
        assert marker not in summary


class TestApplyBudget:
    """Tests for ResultStore.apply_budget."""

    def test_none_budget_passes_through(self, tmp_path: Path) -> None:
        """max_chars=None means no truncation."""
        store = ResultStore(tmp_path)
        big = "x" * 100_000
        assert store.apply_budget("tool", big, None) == big

    def test_within_budget_passes_through(self, tmp_path: Path) -> None:
        """Content under the limit is returned unchanged."""
        store = ResultStore(tmp_path)
        small = "hello world"
        assert store.apply_budget("tool", small, 50_000) == small

    def test_over_budget_persists(self, tmp_path: Path) -> None:
        """Content exceeding the limit is persisted and summarized."""
        store = ResultStore(tmp_path)
        big = "z" * 60_000
        result = store.apply_budget("grep", big, 50_000)

        assert "Output too large" in result
        assert "file_read" in result
        assert len(result) < len(big)

        # File was actually written
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].read_text() == big

    def test_exact_boundary(self, tmp_path: Path) -> None:
        """Content at exactly the limit passes through."""
        store = ResultStore(tmp_path)
        exact = "a" * 50_000
        assert store.apply_budget("tool", exact, 50_000) == exact

    def test_error_output_not_budgeted(self, tmp_path: Path) -> None:
        """Errors should not go through apply_budget (caller responsibility).

        Verify that apply_budget itself doesn't care about error status —
        the orchestrator skips it for errors.
        """
        store = ResultStore(tmp_path)
        error_msg = "x" * 100_000
        # apply_budget truncates regardless — it's the caller's job to skip errors
        result = store.apply_budget("tool", error_msg, 50_000)
        assert "Output too large" in result


class TestDefaultMaxResultChars:
    """Verify the default constant is accessible."""

    def test_default_value(self) -> None:
        assert DEFAULT_MAX_RESULT_CHARS == 50_000
