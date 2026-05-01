"""Tests for GrepTool — regex search across files."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kernel.tools.builtin.grep_tool import GrepTool
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


async def _run(tool: GrepTool, input: dict[str, Any], ctx: ToolContext) -> ToolCallResult:
    results = []
    async for event in tool.call(input, ctx):
        results.append(event)
    assert len(results) == 1
    return results[0]


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------


class TestValidateInput:
    tool = GrepTool()

    async def test_missing_pattern(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="pattern"):
            await self.tool.validate_input({}, _RiskCtx(tmp_path))

    async def test_empty_pattern(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="pattern"):
            await self.tool.validate_input({"pattern": ""}, _RiskCtx(tmp_path))

    async def test_invalid_regex(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="invalid regex"):
            await self.tool.validate_input({"pattern": "[invalid"}, _RiskCtx(tmp_path))

    async def test_valid(self, tmp_path: Path) -> None:
        await self.tool.validate_input({"pattern": r"def\s+\w+"}, _RiskCtx(tmp_path))


# ---------------------------------------------------------------------------
# call()
# ---------------------------------------------------------------------------


class TestCall:
    tool = GrepTool()

    async def test_finds_matches(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("def hello():\n    pass\n")
        (tmp_path / "b.py").write_text("import os\n")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "def"}, ctx)
        assert len(result.data["matches"]) == 1
        assert result.data["matches"][0]["line"] == 1
        assert "hello" in result.data["matches"][0]["text"]

    async def test_no_matches(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello world")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "xyz123"}, ctx)
        assert result.data["matches"] == []

    async def test_case_insensitive(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("Hello\nworld\n")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "hello", "case_insensitive": True}, ctx)
        assert len(result.data["matches"]) == 1

    async def test_case_sensitive_default(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("Hello\nworld\n")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "hello"}, ctx)
        assert len(result.data["matches"]) == 0

    async def test_glob_filter(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("target\n")
        (tmp_path / "b.txt").write_text("target\n")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "target", "glob": "*.py"}, ctx)
        assert len(result.data["matches"]) == 1
        assert result.data["matches"][0]["path"].endswith(".py")

    async def test_custom_path(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "f.txt").write_text("found\n")
        (tmp_path / "f.txt").write_text("found\n")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "found", "path": str(sub)}, ctx)
        assert len(result.data["matches"]) == 1

    async def test_regex_pattern(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("def foo():\ndef bar():\nclass Baz:\n")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": r"def \w+\(\)"}, ctx)
        assert len(result.data["matches"]) == 2

    async def test_multiple_lines_in_file(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("match1\nno\nmatch2\n")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "match"}, ctx)
        assert len(result.data["matches"]) == 2
        assert result.data["matches"][0]["line"] == 1
        assert result.data["matches"][1]["line"] == 3

    async def test_display_type(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello\n")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "hello"}, ctx)
        assert isinstance(result.display, LocationsDisplay)

    async def test_skips_directories(self, tmp_path: Path) -> None:
        """Directories matching glob are silently skipped."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        (tmp_path / "f.txt").write_text("hello\n")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"pattern": "hello"}, ctx)
        assert len(result.data["matches"]) == 1


# ---------------------------------------------------------------------------
# Risk / permission
# ---------------------------------------------------------------------------


class TestRisk:
    tool = GrepTool()

    def test_always_low_risk(self, tmp_path: Path) -> None:
        result = self.tool.default_risk({}, _RiskCtx(tmp_path))
        assert result.risk == "low"

    def test_not_destructive(self) -> None:
        assert not self.tool.is_destructive({})
