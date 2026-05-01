"""Tests for ReplTool — batch execution wrapper for primitive tools."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.builtin.repl import REPL_HIDDEN_TOOLS, ReplTool
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache
from kernel.tools.registry import ToolRegistry
from kernel.tools.tool import Tool
from kernel.tools.types import (
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)


# ---------------------------------------------------------------------------
# Fake tools for testing
# ---------------------------------------------------------------------------


class _FakeReadTool(Tool[dict[str, Any], str]):
    """Simulates a read-only tool (like Glob)."""

    name = "Glob"
    description = "fake glob"
    kind = ToolKind.search

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        pattern = input.get("pattern", "*")
        yield ToolCallResult(
            data={"pattern": pattern},
            llm_content=[TextBlock(text=f"found: {pattern}")],
            display=TextDisplay(text=f"found: {pattern}"),
        )


class _FakeEditTool(Tool[dict[str, Any], str]):
    """Simulates a mutating tool (like FileEdit)."""

    name = "FileEdit"
    description = "fake edit"
    kind = ToolKind.edit

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        path = input.get("file_path", "unknown")
        yield ToolCallResult(
            data={"path": path},
            llm_content=[TextBlock(text=f"edited: {path}")],
            display=TextDisplay(text=f"edited: {path}"),
        )


class _FakeBashTool(Tool[dict[str, Any], str]):
    """Simulates Bash tool (execute kind)."""

    name = "Bash"
    description = "fake bash"
    kind = ToolKind.execute

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        cmd = input.get("command", "")
        yield ToolCallResult(
            data={"command": cmd},
            llm_content=[TextBlock(text=f"output: {cmd}")],
            display=TextDisplay(text=f"output: {cmd}"),
        )


class _FakeFileReadTool(Tool[dict[str, Any], str]):
    """Simulates FileRead tool."""

    name = "FileRead"
    description = "fake file read"
    kind = ToolKind.read

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        path = input.get("file_path", "")
        yield ToolCallResult(
            data={"path": path},
            llm_content=[TextBlock(text=f"content of {path}")],
            display=TextDisplay(text=f"content of {path}"),
        )


class _FakeErrorTool(Tool[dict[str, Any], str]):
    """Tool that raises during call()."""

    name = "WebFetch"
    description = "fake web fetch that errors"
    kind = ToolKind.fetch

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        raise RuntimeError("connection refused")
        yield  # type: ignore[misc]  # make it an async generator


class _NonReplTool(Tool[dict[str, Any], str]):
    """Tool NOT in REPL_HIDDEN_TOOLS (e.g., AskUserQuestion)."""

    name = "AskUserQuestion"
    description = "not managed by REPL"
    kind = ToolKind.think

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        yield ToolCallResult(data={}, llm_content=[], display=TextDisplay(text=""))


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


def _make_registry(*tools: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    for tool in tools:
        reg.register(tool, layer="core")
    return reg


async def _run(tool: ReplTool, input: dict[str, Any], ctx: ToolContext) -> ToolCallResult:
    results: list[ToolCallProgress | ToolCallResult] = []
    async for event in tool.call(input, ctx):
        results.append(event)
    # ReplTool yields exactly one ToolCallResult.
    assert len(results) == 1
    result = results[0]
    assert isinstance(result, ToolCallResult)
    return result


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------


class TestValidateInput:
    @pytest.mark.asyncio
    async def test_calls_must_be_array(self, tmp_path: Path) -> None:
        reg = _make_registry(_FakeReadTool())
        tool = ReplTool(reg)
        risk = _RiskCtx(tmp_path)
        with pytest.raises(ToolInputError, match="calls must be an array"):
            await tool.validate_input({"calls": "not-an-array"}, risk)

    @pytest.mark.asyncio
    async def test_calls_must_not_be_empty(self, tmp_path: Path) -> None:
        reg = _make_registry(_FakeReadTool())
        tool = ReplTool(reg)
        risk = _RiskCtx(tmp_path)
        with pytest.raises(ToolInputError, match="calls must not be empty"):
            await tool.validate_input({"calls": []}, risk)

    @pytest.mark.asyncio
    async def test_tool_name_must_be_string(self, tmp_path: Path) -> None:
        reg = _make_registry(_FakeReadTool())
        tool = ReplTool(reg)
        risk = _RiskCtx(tmp_path)
        with pytest.raises(ToolInputError, match="tool_name must be a non-empty string"):
            await tool.validate_input(
                {"calls": [{"tool_name": 123, "input": {}}]}, risk
            )

    @pytest.mark.asyncio
    async def test_rejects_non_repl_tool(self, tmp_path: Path) -> None:
        reg = _make_registry(_NonReplTool())
        tool = ReplTool(reg)
        risk = _RiskCtx(tmp_path)
        with pytest.raises(ToolInputError, match="not a REPL-managed tool"):
            await tool.validate_input(
                {"calls": [{"tool_name": "AskUserQuestion", "input": {}}]}, risk
            )

    @pytest.mark.asyncio
    async def test_rejects_unknown_tool(self, tmp_path: Path) -> None:
        reg = _make_registry()  # empty registry
        tool = ReplTool(reg)
        risk = _RiskCtx(tmp_path)
        with pytest.raises(ToolInputError, match="not a REPL-managed tool"):
            await tool.validate_input(
                {"calls": [{"tool_name": "NonExistent", "input": {}}]}, risk
            )

    @pytest.mark.asyncio
    async def test_input_must_be_dict(self, tmp_path: Path) -> None:
        reg = _make_registry(_FakeReadTool())
        tool = ReplTool(reg)
        risk = _RiskCtx(tmp_path)
        with pytest.raises(ToolInputError, match="input must be an object"):
            await tool.validate_input(
                {"calls": [{"tool_name": "Glob", "input": "bad"}]}, risk
            )

    @pytest.mark.asyncio
    async def test_valid_input_passes(self, tmp_path: Path) -> None:
        reg = _make_registry(_FakeReadTool())
        tool = ReplTool(reg)
        risk = _RiskCtx(tmp_path)
        # Should not raise.
        await tool.validate_input(
            {"calls": [{"tool_name": "Glob", "input": {"pattern": "*.py"}}]}, risk
        )


# ---------------------------------------------------------------------------
# call() — single tool
# ---------------------------------------------------------------------------


class TestCallSingle:
    @pytest.mark.asyncio
    async def test_single_read_tool(self, tmp_path: Path) -> None:
        reg = _make_registry(_FakeReadTool())
        tool = ReplTool(reg)
        ctx = _make_ctx(tmp_path)

        result = await _run(
            tool,
            {"calls": [{"tool_name": "Glob", "input": {"pattern": "*.txt"}}]},
            ctx,
        )

        assert len(result.data) == 1
        assert result.data[0]["ok"] is True
        assert result.data[0]["tool"] == "Glob"
        # LLM content should contain the XML-tagged result.
        text = result.llm_content[0].text
        assert '<repl_result index="0" tool="Glob">' in text
        assert "found: *.txt" in text

    @pytest.mark.asyncio
    async def test_single_tool_with_id(self, tmp_path: Path) -> None:
        reg = _make_registry(_FakeReadTool())
        tool = ReplTool(reg)
        ctx = _make_ctx(tmp_path)

        result = await _run(
            tool,
            {
                "calls": [
                    {"id": "call-1", "tool_name": "Glob", "input": {"pattern": "*"}}
                ]
            },
            ctx,
        )

        text = result.llm_content[0].text
        assert 'id="call-1"' in text


# ---------------------------------------------------------------------------
# call() — batch / concurrency
# ---------------------------------------------------------------------------


class TestCallBatch:
    @pytest.mark.asyncio
    async def test_multiple_read_tools_run(self, tmp_path: Path) -> None:
        """Multiple read-only tools should all produce results."""
        reg = _make_registry(_FakeReadTool(), _FakeFileReadTool())
        tool = ReplTool(reg)
        ctx = _make_ctx(tmp_path)

        result = await _run(
            tool,
            {
                "calls": [
                    {"tool_name": "Glob", "input": {"pattern": "*.py"}},
                    {"tool_name": "FileRead", "input": {"file_path": "/tmp/x.py"}},
                ]
            },
            ctx,
        )

        assert len(result.data) == 2
        assert result.data[0]["ok"] is True
        assert result.data[1]["ok"] is True
        text = result.llm_content[0].text
        assert 'index="0"' in text
        assert 'index="1"' in text

    @pytest.mark.asyncio
    async def test_mixed_read_write_batch(self, tmp_path: Path) -> None:
        """A batch with both read and write tools should all complete."""
        reg = _make_registry(_FakeReadTool(), _FakeEditTool())
        tool = ReplTool(reg)
        ctx = _make_ctx(tmp_path)

        result = await _run(
            tool,
            {
                "calls": [
                    {"tool_name": "Glob", "input": {"pattern": "*.py"}},
                    {"tool_name": "FileEdit", "input": {"file_path": "foo.py"}},
                ]
            },
            ctx,
        )

        assert len(result.data) == 2
        assert result.data[0]["ok"] is True
        assert result.data[1]["ok"] is True


# ---------------------------------------------------------------------------
# call() — error handling
# ---------------------------------------------------------------------------


class TestCallErrors:
    @pytest.mark.asyncio
    async def test_tool_error_reported_inline(self, tmp_path: Path) -> None:
        """When a tool raises, the error is reported in the result, not re-raised."""
        reg = _make_registry(_FakeReadTool(), _FakeErrorTool())
        tool = ReplTool(reg)
        ctx = _make_ctx(tmp_path)

        result = await _run(
            tool,
            {
                "calls": [
                    {"tool_name": "Glob", "input": {"pattern": "*.py"}},
                    {"tool_name": "WebFetch", "input": {"url": "http://x"}},
                ]
            },
            ctx,
        )

        # First call succeeds, second fails — both reported.
        assert len(result.data) == 2
        assert result.data[0]["ok"] is True
        assert "error" in result.data[1]
        text = result.llm_content[0].text
        assert 'error="true"' in text
        assert "connection refused" in text

    @pytest.mark.asyncio
    async def test_inner_validation_error(self, tmp_path: Path) -> None:
        """When inner tool.validate_input raises, error is reported inline."""

        class _ValidatingTool(Tool[dict[str, Any], str]):
            name = "Grep"
            description = "fake grep"
            kind = ToolKind.search

            async def validate_input(self, input: dict[str, Any], ctx: Any) -> None:
                if "pattern" not in input:
                    raise ToolInputError("pattern required")

            async def call(
                self, input: dict[str, Any], ctx: Any
            ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
                yield ToolCallResult(
                    data={},
                    llm_content=[TextBlock(text="ok")],
                    display=TextDisplay(text="ok"),
                )

        reg = _make_registry(_ValidatingTool())
        tool = ReplTool(reg)
        ctx = _make_ctx(tmp_path)

        result = await _run(
            tool,
            {"calls": [{"tool_name": "Grep", "input": {}}]},
            ctx,
        )

        assert "error" in result.data[0]
        text = result.llm_content[0].text
        assert "Input validation failed" in text


# ---------------------------------------------------------------------------
# default_risk
# ---------------------------------------------------------------------------


class TestDefaultRisk:
    def test_read_only_batch_is_low_risk(self, tmp_path: Path) -> None:
        reg = _make_registry(_FakeReadTool())
        tool = ReplTool(reg)
        risk = _RiskCtx(tmp_path)
        suggestion = tool.default_risk(
            {"calls": [{"tool_name": "Glob", "input": {}}]}, risk
        )
        assert suggestion.risk == "low"
        assert suggestion.default_decision == "allow"

    def test_mutating_batch_delegates_to_inner_tool(self, tmp_path: Path) -> None:
        """REPL delegates risk to inner tool's default_risk, not just kind."""
        reg = _make_registry(_FakeReadTool(), _FakeEditTool())
        tool = ReplTool(reg)
        risk = _RiskCtx(tmp_path)
        suggestion = tool.default_risk(
            {
                "calls": [
                    {"tool_name": "Glob", "input": {}},
                    {"tool_name": "FileEdit", "input": {}},
                ]
            },
            risk,
        )
        # _FakeEditTool uses the base class default_risk (low, ask).
        # REPL delegates to it rather than blindly marking medium.
        assert suggestion.default_decision == "ask"


# ---------------------------------------------------------------------------
# activity_description
# ---------------------------------------------------------------------------


class TestActivityDescription:
    def test_activity_lists_tools(self) -> None:
        reg = _make_registry(_FakeReadTool())
        tool = ReplTool(reg)
        desc = tool.activity_description(
            {"calls": [{"tool_name": "Glob", "input": {}}]}
        )
        assert desc is not None
        assert "1 tools" in desc
        assert "Glob" in desc

    def test_empty_calls(self) -> None:
        reg = _make_registry()
        tool = ReplTool(reg)
        desc = tool.activity_description({"calls": []})
        assert desc is not None
        assert "empty batch" in desc


# ---------------------------------------------------------------------------
# REPL_HIDDEN_TOOLS constant
# ---------------------------------------------------------------------------


class TestHiddenTools:
    def test_expected_tools_in_set(self) -> None:
        expected = {"Bash", "FileRead", "FileEdit", "FileWrite", "Glob", "Grep", "Agent"}
        assert expected.issubset(REPL_HIDDEN_TOOLS)

    def test_control_tools_not_in_set(self) -> None:
        control = {"AskUserQuestion", "EnterPlanMode", "ExitPlanMode", "SkillTool"}
        assert control.isdisjoint(REPL_HIDDEN_TOOLS)


# ---------------------------------------------------------------------------
# Registry snapshot with repl_mode
# ---------------------------------------------------------------------------


class TestSnapshotReplMode:
    def test_repl_mode_hides_tools_from_schemas(self) -> None:
        reg = ToolRegistry()
        reg.register(_FakeReadTool(), layer="core")  # Glob — hidden
        reg.register(_NonReplTool(), layer="core")  # AskUserQuestion — visible
        repl = ReplTool(reg)
        reg.register(repl, layer="core")

        snap = reg.snapshot(repl_mode=True)
        schema_names = [s.name for s in snap.schemas]

        assert "Glob" not in schema_names
        assert "AskUserQuestion" in schema_names
        assert "REPL" in schema_names

    def test_repl_mode_keeps_hidden_tools_in_lookup(self) -> None:
        reg = ToolRegistry()
        reg.register(_FakeReadTool(), layer="core")
        repl = ReplTool(reg)
        reg.register(repl, layer="core")

        snap = reg.snapshot(repl_mode=True)

        # Glob should NOT be in schemas but SHOULD be in lookup.
        schema_names = [s.name for s in snap.schemas]
        assert "Glob" not in schema_names
        assert "Glob" in snap.lookup

    def test_repl_mode_off_shows_all_tools(self) -> None:
        reg = ToolRegistry()
        reg.register(_FakeReadTool(), layer="core")
        reg.register(_NonReplTool(), layer="core")

        snap = reg.snapshot(repl_mode=False)
        schema_names = [s.name for s in snap.schemas]

        assert "Glob" in schema_names
        assert "AskUserQuestion" in schema_names

    def test_repl_mode_combined_with_plan_mode(self) -> None:
        """plan_mode + repl_mode should both apply their filters."""
        reg = ToolRegistry()
        reg.register(_FakeReadTool(), layer="core")  # search — not mutating
        reg.register(_FakeEditTool(), layer="core")  # edit — mutating
        reg.register(_NonReplTool(), layer="core")  # think — not hidden
        repl = ReplTool(reg)
        reg.register(repl, layer="core")

        snap = reg.snapshot(repl_mode=True, plan_mode=True)
        schema_names = [s.name for s in snap.schemas]

        # Glob: hidden by REPL
        assert "Glob" not in schema_names
        # FileEdit: hidden by REPL (and would also be excluded by plan_mode)
        assert "FileEdit" not in schema_names
        # AskUserQuestion: visible (not hidden, not mutating)
        assert "AskUserQuestion" in schema_names
        # REPL itself: execute kind, excluded by plan_mode
        assert "REPL" not in schema_names
