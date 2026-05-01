"""Tests for FileWriteTool — file creation and overwriting."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kernel.tools.builtin.file_write import FileWriteTool
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache
from kernel.tools.types import DiffDisplay, ToolCallResult, ToolInputError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RiskCtx:
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


async def _run(tool: FileWriteTool, input: dict[str, Any], ctx: ToolContext) -> ToolCallResult:
    results = []
    async for event in tool.call(input, ctx):
        results.append(event)
    assert len(results) == 1
    return results[0]


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------


class TestValidateInput:
    tool = FileWriteTool()

    async def test_missing_path(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="path"):
            await self.tool.validate_input({"content": "x"}, _RiskCtx(tmp_path))

    async def test_non_string_content(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="content"):
            await self.tool.validate_input({"path": "f.txt", "content": 123}, _RiskCtx(tmp_path))

    async def test_valid(self, tmp_path: Path) -> None:
        await self.tool.validate_input({"path": "f.txt", "content": "hello"}, _RiskCtx(tmp_path))


# ---------------------------------------------------------------------------
# default_risk
# ---------------------------------------------------------------------------


class TestDefaultRisk:
    tool = FileWriteTool()

    def test_outside_cwd(self, tmp_path: Path) -> None:
        result = self.tool.default_risk({"path": "/tmp/outside.txt"}, _RiskCtx(tmp_path))
        assert result.risk == "high"

    def test_overwrite_existing(self, tmp_path: Path) -> None:
        f = tmp_path / "exists.txt"
        f.write_text("old")
        result = self.tool.default_risk({"path": str(f)}, _RiskCtx(tmp_path))
        assert result.risk == "medium"

    def test_new_file_in_cwd(self, tmp_path: Path) -> None:
        result = self.tool.default_risk({"path": str(tmp_path / "new.txt")}, _RiskCtx(tmp_path))
        assert result.risk == "low"
        assert result.default_decision == "allow"


# ---------------------------------------------------------------------------
# is_destructive
# ---------------------------------------------------------------------------


class TestIsDestructive:
    tool = FileWriteTool()

    def test_new_file(self, tmp_path: Path) -> None:
        assert not self.tool.is_destructive({"path": str(tmp_path / "new.txt")})

    def test_existing_file(self, tmp_path: Path) -> None:
        f = tmp_path / "exists.txt"
        f.write_text("old")
        assert self.tool.is_destructive({"path": str(f)})

    def test_empty_path(self) -> None:
        assert not self.tool.is_destructive({"path": ""})


# ---------------------------------------------------------------------------
# call()
# ---------------------------------------------------------------------------


class TestCall:
    tool = FileWriteTool()

    async def test_create_new_file(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        path = tmp_path / "new.txt"
        result = await _run(self.tool, {"path": str(path), "content": "hello"}, ctx)
        assert result.data["action"] == "wrote"
        assert path.read_text() == "hello"

    async def test_create_with_nested_dirs(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        path = tmp_path / "a" / "b" / "c.txt"
        result = await _run(self.tool, {"path": str(path), "content": "deep"}, ctx)
        assert result.data["action"] == "wrote"
        assert path.read_text() == "deep"

    async def test_overwrite_existing(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("old")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(f), "content": "new"}, ctx)
        assert result.data["action"] == "overwrote"
        assert f.read_text() == "new"

    async def test_stale_file_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("current on disk")
        ctx = _make_ctx(tmp_path)
        ctx.file_state.record(f, "stale content")
        result = await _run(self.tool, {"path": str(f), "content": "new"}, ctx)
        assert "changed on disk" in result.data["error"]
        assert f.read_text() == "current on disk"  # unchanged

    async def test_invalidates_cache_after_write(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        path = tmp_path / "f.txt"
        await _run(self.tool, {"path": str(path), "content": "hello"}, ctx)
        assert ctx.file_state.verify(path) is None

    async def test_relative_path(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        await _run(self.tool, {"path": "rel.txt", "content": "hello"}, ctx)
        assert (tmp_path / "rel.txt").read_text() == "hello"

    async def test_display_is_diff(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("old")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(f), "content": "new"}, ctx)
        assert isinstance(result.display, DiffDisplay)
        assert result.display.before == "old"
        assert result.display.after == "new"

    async def test_new_file_diff_before_is_none(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(tmp_path / "new.txt"), "content": "hello"}, ctx)
        assert isinstance(result.display, DiffDisplay)
        assert result.display.before is None
