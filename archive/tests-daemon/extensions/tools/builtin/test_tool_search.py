"""Tests for Phase 5.5.4H — ToolSearch tool and registry core/lazy split."""

from __future__ import annotations

import json

import pytest

from daemon.extensions.tools.base import PermissionLevel, Tool, ToolContext, ToolResult
from daemon.extensions.tools.builtin.tool_search import ToolSearchTool
from daemon.extensions.tools.registry import CORE_TOOL_NAMES, ToolRegistry


# -- Dummy tools for testing -------------------------------------------------


from pydantic import BaseModel as _BaseModel


class _DummyInput(_BaseModel):
    pass


class _DummyTool(Tool):
    permission_level = PermissionLevel.NONE
    Input = _DummyInput

    def __init__(self, name: str, description: str = "A test tool") -> None:
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(output="ok")


def _build_registry() -> ToolRegistry:
    """Build a registry with a mix of core and lazy tools."""
    reg = ToolRegistry()
    # Core tools
    reg.register(_DummyTool("bash", "Execute shell commands"))
    reg.register(_DummyTool("file_read", "Read a file"))
    reg.register(_DummyTool("grep", "Search file contents"))
    reg.register(_DummyTool("http_fetch", "Fetch a URL"))
    reg.register(_DummyTool("web_search", "Search the web"))
    # Lazy tools
    reg.register(_DummyTool("memory_write", "Write to memory"))
    reg.register(_DummyTool("memory_list", "List memories"))
    return reg


# -- Registry core/lazy split -------------------------------------------


class TestRegistrySplit:
    def test_core_tools_classified(self) -> None:
        reg = _build_registry()
        core_defs = reg.get_core_definitions()
        core_names = {d.name for d in core_defs}
        assert "bash" in core_names
        assert "file_read" in core_names
        assert "grep" in core_names

    def test_lazy_tools_not_in_core(self) -> None:
        reg = _build_registry()
        core_names = {d.name for d in reg.get_core_definitions()}
        assert "memory_write" not in core_names
        assert "memory_list" not in core_names

    def test_get_definitions_returns_all(self) -> None:
        reg = _build_registry()
        all_defs = reg.get_definitions()
        all_names = {d.name for d in all_defs}
        assert "bash" in all_names
        assert "http_fetch" in all_names
        assert len(all_names) == 7

    def test_get_finds_both(self) -> None:
        reg = _build_registry()
        assert reg.get("bash") is not None
        assert reg.get("http_fetch") is not None
        assert reg.get("nonexistent") is None

    def test_lazy_tool_names(self) -> None:
        reg = _build_registry()
        names = reg.lazy_tool_names
        assert "memory_write" in names
        assert "bash" not in names
        assert "http_fetch" not in names  # now core

    def test_lazy_count(self) -> None:
        reg = _build_registry()
        assert reg.lazy_count == 2  # memory_write, memory_list

    def test_len_includes_both(self) -> None:
        reg = _build_registry()
        assert len(reg) == 7

    def test_contains_both(self) -> None:
        reg = _build_registry()
        assert "bash" in reg
        assert "http_fetch" in reg
        assert "nope" not in reg

    def test_clone_preserves_split(self) -> None:
        reg = _build_registry()
        child = reg.clone({"bash", "http_fetch"})
        assert len(child) == 2
        assert child.get("bash") is not None
        assert child.get("http_fetch") is not None

    def test_unregister_core(self) -> None:
        reg = _build_registry()
        assert reg.unregister("bash")
        assert reg.get("bash") is None

    def test_unregister_lazy(self) -> None:
        reg = _build_registry()
        assert reg.unregister("http_fetch")
        assert reg.get("http_fetch") is None

    def test_get_definition_single(self) -> None:
        reg = _build_registry()
        defn = reg.get_definition("http_fetch")
        assert defn is not None
        assert defn.name == "http_fetch"
        assert reg.get_definition("nonexistent") is None


# -- Registry search ---------------------------------------------------------


class TestRegistrySearch:
    def test_exact_name_match(self) -> None:
        reg = _build_registry()
        results = reg.search("memory_write")
        assert len(results) >= 1
        assert results[0].name == "memory_write"  # exact match ranked first

    def test_prefix_match(self) -> None:
        reg = _build_registry()
        results = reg.search("memory")
        names = {r.name for r in results}
        assert "memory_write" in names
        assert "memory_list" in names

    def test_keyword_in_name(self) -> None:
        reg = _build_registry()
        results = reg.search("list")
        names = {r.name for r in results}
        assert "memory_list" in names

    def test_keyword_in_description(self) -> None:
        reg = _build_registry()
        results = reg.search("Write to")
        assert len(results) >= 1
        assert results[0].name == "memory_write"

    def test_no_match(self) -> None:
        reg = _build_registry()
        results = reg.search("zzz_nonexistent")
        assert results == []

    def test_max_results(self) -> None:
        reg = _build_registry()
        results = reg.search("me", max_results=2)  # matches memory_write, memory_list
        assert len(results) <= 2

    def test_case_insensitive(self) -> None:
        reg = _build_registry()
        results = reg.search("MEMORY")
        assert len(results) >= 1


# -- ToolSearchTool ----------------------------------------------------------


class TestToolSearchTool:
    @pytest.mark.asyncio
    async def test_select_exact(self) -> None:
        reg = _build_registry()
        tool = ToolSearchTool(registry=reg)
        ctx = ToolContext(cwd="/tmp")
        result = await tool.execute({"query": "select:http_fetch"}, ctx)
        data = json.loads(result.output)
        assert len(data["tools"]) == 1
        assert data["tools"][0]["name"] == "http_fetch"

    @pytest.mark.asyncio
    async def test_select_multiple(self) -> None:
        reg = _build_registry()
        tool = ToolSearchTool(registry=reg)
        ctx = ToolContext(cwd="/tmp")
        result = await tool.execute({"query": "select:http_fetch,memory_write"}, ctx)
        data = json.loads(result.output)
        assert len(data["tools"]) == 2

    @pytest.mark.asyncio
    async def test_keyword_search(self) -> None:
        reg = _build_registry()
        tool = ToolSearchTool(registry=reg)
        ctx = ToolContext(cwd="/tmp")
        result = await tool.execute({"query": "memory"}, ctx)
        data = json.loads(result.output)
        assert len(data["tools"]) >= 2
        assert data["total_lazy"] == 2

    @pytest.mark.asyncio
    async def test_select_nonexistent(self) -> None:
        reg = _build_registry()
        tool = ToolSearchTool(registry=reg)
        ctx = ToolContext(cwd="/tmp")
        result = await tool.execute({"query": "select:nonexistent"}, ctx)
        data = json.loads(result.output)
        assert len(data["tools"]) == 0

    def test_tool_metadata(self) -> None:
        reg = _build_registry()
        tool = ToolSearchTool(registry=reg)
        assert tool.name == "tool_search"
        assert tool.name in CORE_TOOL_NAMES
