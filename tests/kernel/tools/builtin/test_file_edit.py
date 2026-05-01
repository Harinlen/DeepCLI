"""Tests for FileEditTool — exact string replacement in files."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kernel.tools.builtin.file_edit import FileEditTool
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache
from kernel.tools.types import DiffDisplay, ToolCallResult, ToolInputError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RiskCtx:
    """Minimal RiskContext satisfying the Protocol."""

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    @property
    def cwd(self) -> Path:
        return self._cwd

    @property
    def session_id(self) -> str:
        return "test-session"


def _make_ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="test-session",
        agent_depth=0,
        agent_id=None,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
    )


async def _run(tool: FileEditTool, input: dict[str, Any], ctx: ToolContext) -> ToolCallResult:
    results = []
    async for event in tool.call(input, ctx):
        results.append(event)
    assert len(results) == 1
    return results[0]


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------


class TestValidateInput:
    tool = FileEditTool()

    async def test_missing_path(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="path"):
            await self.tool.validate_input({"old_string": "a", "new_string": "b"}, _RiskCtx(tmp_path))

    async def test_empty_path(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="path"):
            await self.tool.validate_input(
                {"path": "", "old_string": "a", "new_string": "b"}, _RiskCtx(tmp_path)
            )

    async def test_non_string_old(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="old_string"):
            await self.tool.validate_input(
                {"path": "f.txt", "old_string": 123, "new_string": "b"}, _RiskCtx(tmp_path)
            )

    async def test_non_string_new(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="new_string"):
            await self.tool.validate_input(
                {"path": "f.txt", "old_string": "a", "new_string": 123}, _RiskCtx(tmp_path)
            )

    async def test_identical_strings(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="identical"):
            await self.tool.validate_input(
                {"path": "f.txt", "old_string": "same", "new_string": "same"}, _RiskCtx(tmp_path)
            )

    async def test_valid_input(self, tmp_path: Path) -> None:
        await self.tool.validate_input(
            {"path": "f.txt", "old_string": "a", "new_string": "b"}, _RiskCtx(tmp_path)
        )


# ---------------------------------------------------------------------------
# default_risk
# ---------------------------------------------------------------------------


class TestDefaultRisk:
    tool = FileEditTool()

    def test_within_cwd(self, tmp_path: Path) -> None:
        path = tmp_path / "foo.txt"
        result = self.tool.default_risk({"path": str(path)}, _RiskCtx(tmp_path))
        assert result.risk == "low"
        assert result.default_decision == "allow"

    def test_outside_cwd(self, tmp_path: Path) -> None:
        result = self.tool.default_risk({"path": "/etc/passwd"}, _RiskCtx(tmp_path))
        assert result.risk == "high"
        assert result.default_decision == "ask"

    def test_empty_path(self, tmp_path: Path) -> None:
        result = self.tool.default_risk({"path": ""}, _RiskCtx(tmp_path))
        assert result.risk == "high"


# ---------------------------------------------------------------------------
# call()
# ---------------------------------------------------------------------------


class TestCall:
    tool = FileEditTool()

    async def test_file_not_found(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(tmp_path / "nope.txt"), "old_string": "a", "new_string": "b"}, ctx)
        assert "not found" in result.data["error"]

    async def test_old_string_not_found(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("hello world")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(f), "old_string": "xyz", "new_string": "abc"}, ctx)
        assert "not found" in result.data["error"]

    async def test_ambiguous_match(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("aaa")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(f), "old_string": "a", "new_string": "b"}, ctx)
        assert "multiple times" in result.data["error"]

    async def test_single_replace(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("hello world")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(f), "old_string": "hello", "new_string": "goodbye"}, ctx)
        assert result.data["replaced"] is True
        assert f.read_text() == "goodbye world"

    async def test_replace_all(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("aXaXa")
        ctx = _make_ctx(tmp_path)
        result = await _run(
            self.tool,
            {"path": str(f), "old_string": "a", "new_string": "b", "replace_all": True},
            ctx,
        )
        assert result.data["replaced"] is True
        assert f.read_text() == "bXbXb"

    async def test_stale_file_rejected(self, tmp_path: Path) -> None:
        """If file changed on disk since last read, edit is rejected."""
        f = tmp_path / "f.txt"
        f.write_text("original")
        ctx = _make_ctx(tmp_path)
        # Record state with old content
        ctx.file_state.record(f, "old content that differs")
        result = await _run(self.tool, {"path": str(f), "old_string": "original", "new_string": "new"}, ctx)
        assert "changed on disk" in result.data["error"]

    async def test_invalidates_cache_after_write(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("hello")
        ctx = _make_ctx(tmp_path)
        await _run(self.tool, {"path": str(f), "old_string": "hello", "new_string": "bye"}, ctx)
        assert ctx.file_state.verify(f) is None

    async def test_relative_path_resolved(self, tmp_path: Path) -> None:
        f = tmp_path / "sub" / "f.txt"
        f.parent.mkdir()
        f.write_text("hello")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": "sub/f.txt", "old_string": "hello", "new_string": "bye"}, ctx)
        assert result.data["replaced"] is True
        assert f.read_text() == "bye"

    async def test_display_is_diff(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("hello")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(f), "old_string": "hello", "new_string": "bye"}, ctx)
        assert isinstance(result.display, DiffDisplay)
        assert result.display.before == "hello"
        assert result.display.after == "bye"


# ---------------------------------------------------------------------------
# permission matcher
# ---------------------------------------------------------------------------


class TestPermissionMatcher:
    tool = FileEditTool()

    def test_matches_glob(self) -> None:
        matcher = self.tool.prepare_permission_matcher({"path": "/home/user/project/src/main.py"})
        assert matcher("*.py")
        assert not matcher("*.js")

    def test_not_destructive(self) -> None:
        assert not self.tool.is_destructive({"path": "any"})
