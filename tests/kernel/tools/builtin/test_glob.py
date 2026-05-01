"""Tests for GlobTool — path-pattern file search."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kernel.tools.builtin.glob_tool import GlobTool
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache
from kernel.tools.types import LocationsDisplay, ToolCallResult, ToolInputError


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


async def _run(tool: GlobTool, input: dict[str, Any], ctx: ToolContext) -> ToolCallResult:
    results = []
    async for event in tool.call(input, ctx):
        results.append(event)
    assert len(results) == 1
    return results[0]


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------


class TestValidateInput:
    tool = GlobTool()

    async def test_missing_pattern(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="pattern"):
            await self.tool.validate_input({}, _RiskCtx(tmp_path))

    async def test_empty_pattern(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="pattern"):
            await self.tool.validate_input({"pattern": ""}, _RiskCtx(tmp_path))

    async def test_valid(self, tmp_path: Path) -> None:
        await self.tool.validate_input({"pattern": "*.py"}, _RiskCtx(tmp_path))


# ---------------------------------------------------------------------------
# call()
# ---------------------------------------------------------------------------


class TestCall:
    tool = GlobTool()

    async def test_finds_matching_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("x")
        (tmp_path / "c.txt").write_text("x")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "*.py"}, ctx)
        assert len(result.data["matches"]) == 2
        assert all(m.endswith(".py") for m in result.data["matches"])

    async def test_no_matches(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "*.xyz"}, ctx)
        assert result.data["matches"] == []

    async def test_recursive_pattern(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("x")
        (tmp_path / "top.py").write_text("x")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "**/*.py"}, ctx)
        assert len(result.data["matches"]) == 2

    async def test_custom_base_path(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "f.txt").write_text("x")
        (tmp_path / "f.txt").write_text("x")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "*.txt", "path": str(sub)}, ctx)
        assert len(result.data["matches"]) == 1

    async def test_relative_base_path(self, tmp_path: Path) -> None:
        sub = tmp_path / "mydir"
        sub.mkdir()
        (sub / "f.txt").write_text("x")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "*.txt", "path": "mydir"}, ctx)
        assert len(result.data["matches"]) == 1

    async def test_display_type(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "*.py"}, ctx)
        assert isinstance(result.display, LocationsDisplay)

    async def test_sorted_newest_first(self, tmp_path: Path) -> None:
        import time

        f1 = tmp_path / "old.py"
        f1.write_text("x")
        time.sleep(0.05)
        f2 = tmp_path / "new.py"
        f2.write_text("x")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "*.py"}, ctx)
        assert result.data["matches"][0].endswith("new.py")


# ---------------------------------------------------------------------------
# Risk / permission
# ---------------------------------------------------------------------------


class TestRisk:
    tool = GlobTool()

    def test_always_low_risk(self, tmp_path: Path) -> None:
        result = self.tool.default_risk({}, _RiskCtx(tmp_path))
        assert result.risk == "low"

    def test_not_destructive(self) -> None:
        assert not self.tool.is_destructive({})
