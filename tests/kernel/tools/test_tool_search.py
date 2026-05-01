"""ToolSearchTool — select / freetext / promote semantics."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from kernel.orchestrator.types import ToolKind
from kernel.tools.builtin.tool_search import ToolSearchTool
from kernel.tools.context import ToolContext
from kernel.tools.registry import ToolRegistry
from kernel.tools.tool import Tool
from kernel.tools.types import ToolCallProgress, ToolCallResult


# ---------------------------------------------------------------------------
# Fixtures — fake deferred tools
# ---------------------------------------------------------------------------


class _FakeDeferred(Tool[dict[str, Any], str]):
    name = "FakeAlpha"
    description = "A fake alpha tool for testing"
    kind = ToolKind.read
    should_defer = True
    search_hint = "alpha testing fake"

    async def call(
        self, input: dict[str, Any], ctx: Any,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        yield ToolCallResult(data={}, llm_content=[], display=None)  # type: ignore[arg-type]


class _FakeDeferredB(_FakeDeferred):
    name = "FakeBeta"
    description = "A fake beta tool for notebooks"
    search_hint = "beta notebook jupyter"


class _FakeDeferredC(_FakeDeferred):
    name = "FakeGamma"
    description = "A fake gamma tool"
    search_hint = "gamma data pipeline"


class _FakeCore(_FakeDeferred):
    """A core (non-deferred) tool — should NOT appear in search results."""
    name = "CoreTool"
    should_defer = False


def _make_registry(*deferred_classes: type[Tool], include_core: bool = False) -> ToolRegistry:
    """Build a registry with the given deferred tools."""
    reg = ToolRegistry()
    for cls in deferred_classes:
        reg.register(cls(), layer="deferred")
    if include_core:
        reg.register(_FakeCore(), layer="core")
    return reg


def _make_tool(registry: ToolRegistry) -> ToolSearchTool:
    return ToolSearchTool(registry)


async def _run_search(
    tool: ToolSearchTool, query: str, max_results: int = 5,
) -> ToolCallResult:
    """Run ToolSearchTool.call() and return the final ToolCallResult."""
    ctx = ToolContext(
        session_id="test",
        agent_depth=0,
        agent_id=None,
        cwd=__import__("pathlib").Path.cwd(),
        cancel_event=__import__("asyncio").Event(),
        file_state=__import__("kernel.tools.file_state", fromlist=["FileStateCache"]).FileStateCache(),
    )
    result = None
    async for event in tool.call({"query": query, "max_results": max_results}, ctx):
        if isinstance(event, ToolCallResult):
            result = event
    assert result is not None, "ToolSearchTool.call() must yield a ToolCallResult"
    return result


# ---------------------------------------------------------------------------
# select: mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_exact_match() -> None:
    reg = _make_registry(_FakeDeferred, _FakeDeferredB)
    tool = _make_tool(reg)
    result = await _run_search(tool, "select:FakeAlpha")

    text = result.llm_content[0].text  # type: ignore[attr-defined]
    assert "<functions>" in text
    assert "FakeAlpha" in text
    assert "FakeBeta" not in text


@pytest.mark.asyncio
async def test_select_multiple() -> None:
    reg = _make_registry(_FakeDeferred, _FakeDeferredB)
    tool = _make_tool(reg)
    result = await _run_search(tool, "select:FakeAlpha,FakeBeta")

    text = result.llm_content[0].text  # type: ignore[attr-defined]
    assert "FakeAlpha" in text
    assert "FakeBeta" in text


@pytest.mark.asyncio
async def test_select_unknown_name() -> None:
    reg = _make_registry(_FakeDeferred)
    tool = _make_tool(reg)
    result = await _run_search(tool, "select:Nonexistent")

    text = result.llm_content[0].text  # type: ignore[attr-defined]
    assert "No deferred tools matched" in text
    assert "FakeAlpha" in text  # listed as available


# ---------------------------------------------------------------------------
# Freetext mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_freetext_matches_name() -> None:
    reg = _make_registry(_FakeDeferred, _FakeDeferredB)
    tool = _make_tool(reg)
    result = await _run_search(tool, "Alpha")

    text = result.llm_content[0].text  # type: ignore[attr-defined]
    assert "FakeAlpha" in text


@pytest.mark.asyncio
async def test_freetext_matches_search_hint() -> None:
    reg = _make_registry(_FakeDeferred, _FakeDeferredB)
    tool = _make_tool(reg)
    result = await _run_search(tool, "notebook jupyter")

    text = result.llm_content[0].text  # type: ignore[attr-defined]
    assert "FakeBeta" in text


@pytest.mark.asyncio
async def test_freetext_no_match() -> None:
    reg = _make_registry(_FakeDeferred)
    tool = _make_tool(reg)
    result = await _run_search(tool, "xyznonexistent")

    text = result.llm_content[0].text  # type: ignore[attr-defined]
    assert "No deferred tools matched" in text


@pytest.mark.asyncio
async def test_freetext_no_deferred_tools() -> None:
    reg = _make_registry()  # empty
    tool = _make_tool(reg)
    result = await _run_search(tool, "anything")

    text = result.llm_content[0].text  # type: ignore[attr-defined]
    assert "No deferred tools are currently registered" in text


# ---------------------------------------------------------------------------
# +prefix mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plus_match_requires_prefix() -> None:
    reg = _make_registry(_FakeDeferred, _FakeDeferredB, _FakeDeferredC)
    tool = _make_tool(reg)
    result = await _run_search(tool, "+Fake data")

    text = result.llm_content[0].text  # type: ignore[attr-defined]
    # FakeGamma has "data pipeline" in search_hint — should rank highest
    assert "FakeGamma" in text


# ---------------------------------------------------------------------------
# max_results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_results_caps_output() -> None:
    reg = _make_registry(_FakeDeferred, _FakeDeferredB, _FakeDeferredC)
    tool = _make_tool(reg)
    result = await _run_search(tool, "fake", max_results=1)

    # Should only return 1 result
    assert len(result.data) == 1


# ---------------------------------------------------------------------------
# promote semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_moves_to_core() -> None:
    reg = _make_registry(_FakeDeferred)
    tool = _make_tool(reg)

    # Before search, tool is deferred.
    snap_before = reg.snapshot()
    assert "FakeAlpha" in snap_before.deferred_names

    await _run_search(tool, "select:FakeAlpha")

    # After search, tool is promoted to core.
    snap_after = reg.snapshot()
    assert "FakeAlpha" not in snap_after.deferred_names
    schema_names = [s.name for s in snap_after.schemas]
    assert "FakeAlpha" in schema_names


@pytest.mark.asyncio
async def test_snapshot_after_promote_includes_schema() -> None:
    reg = _make_registry(_FakeDeferred, _FakeDeferredB)
    tool = _make_tool(reg)

    # Promote only FakeAlpha via search.
    await _run_search(tool, "select:FakeAlpha")

    snap = reg.snapshot()
    schema_names = [s.name for s in snap.schemas]
    assert "FakeAlpha" in schema_names
    # FakeBeta remains deferred.
    assert "FakeBeta" not in schema_names
    assert "FakeBeta" in snap.deferred_names


# ---------------------------------------------------------------------------
# Core tools excluded from search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_core_tools_not_searchable() -> None:
    """Core-layer tools must not appear in ToolSearch results."""
    reg = _make_registry(_FakeDeferred, include_core=True)
    tool = _make_tool(reg)
    result = await _run_search(tool, "CoreTool")

    text = result.llm_content[0].text  # type: ignore[attr-defined]
    # CoreTool is core, not deferred — should not match.
    assert "CoreTool" not in text or "No deferred tools matched" in text


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_output_format_is_functions_block() -> None:
    reg = _make_registry(_FakeDeferred)
    tool = _make_tool(reg)
    result = await _run_search(tool, "select:FakeAlpha")

    text = result.llm_content[0].text  # type: ignore[attr-defined]
    assert text.startswith("<functions>")
    assert text.strip().endswith("</functions>")
    # Each tool wrapped in <function>...</function>
    assert "<function>" in text
    assert "</function>" in text
    # Inner content is valid JSON
    start = text.index("<function>") + len("<function>")
    end = text.index("</function>")
    inner = text[start:end]
    parsed = json.loads(inner)
    assert parsed["name"] == "FakeAlpha"
    assert "description" in parsed
    assert "parameters" in parsed


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_empty_query_raises() -> None:
    reg = _make_registry()
    tool = _make_tool(reg)
    from kernel.tools.types import ToolInputError

    with pytest.raises(ToolInputError):
        await tool.validate_input({"query": ""}, None)  # type: ignore[arg-type]

    with pytest.raises(ToolInputError):
        await tool.validate_input({"query": "   "}, None)  # type: ignore[arg-type]
