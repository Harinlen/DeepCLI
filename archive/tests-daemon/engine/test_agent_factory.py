"""Tests for the sub-agent factory (Phase 5.2).

Verifies child orchestrator construction, tool registry filtering,
depth limiting, and permission inheritance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from daemon.config.schema import AgentRuntimeConfig
from daemon.engine.orchestrator import Orchestrator
from daemon.engine.orchestrator.agent_factory import AgentFactory, _build_child_tool_registry
from daemon.extensions.tools.base import (
    ConcurrencyHint,
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.extensions.tools.builtin.agent_tool import AgentTool
from daemon.extensions.tools.registry import ToolRegistry
from daemon.permissions.modes import PermissionMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTool(Tool):
    """Minimal tool for tests."""

    name = "fake"
    description = "fake"
    permission_level = PermissionLevel.NONE

    class Input:
        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output="ok")


class _FakeGrepTool(Tool):
    name = "grep"
    description = "grep"
    permission_level = PermissionLevel.NONE
    concurrency = ConcurrencyHint.PARALLEL

    class Input:
        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output="hits")


def _make_parent(tmp_path: Path) -> Orchestrator:
    """Build a minimal parent orchestrator."""
    from tests.daemon.engine.orchestrator_test_utils import make_test_orchestrator

    from daemon.providers.base import ModelInfo, Provider

    class _DummyProvider(Provider):
        name = "dummy"

        async def stream(self, *_a: Any, **_kw: Any) -> Any:
            if False:
                yield  # type: ignore[unreachable]

        async def models(self) -> list[ModelInfo]:
            return []

    tool_registry = ToolRegistry()
    tool_registry.register(_FakeTool())
    tool_registry.register(_FakeGrepTool())
    tool_registry.register(AgentTool())

    orch = make_test_orchestrator(
        provider=_DummyProvider(),
        tmp_path=tmp_path,
        tool_registry=tool_registry,
    )
    orch.compactor.context_window = 100_000
    return orch


# ---------------------------------------------------------------------------
# Tests: ToolRegistry.clone()
# ---------------------------------------------------------------------------


class TestToolRegistryClone:
    def test_clone_all(self) -> None:
        reg = ToolRegistry()
        reg.register(_FakeTool())
        reg.register(_FakeGrepTool())

        child = reg.clone()
        assert len(child) == 2
        assert "fake" in child
        assert "grep" in child

    def test_clone_subset(self) -> None:
        reg = ToolRegistry()
        reg.register(_FakeTool())
        reg.register(_FakeGrepTool())

        child = reg.clone(names={"grep"})
        assert len(child) == 1
        assert "grep" in child
        assert "fake" not in child

    def test_clone_shares_instances(self) -> None:
        reg = ToolRegistry()
        tool = _FakeTool()
        reg.register(tool)

        child = reg.clone()
        assert child.get("fake") is tool

    def test_clone_empty_names(self) -> None:
        reg = ToolRegistry()
        reg.register(_FakeTool())

        child = reg.clone(names=set())
        assert len(child) == 0


# ---------------------------------------------------------------------------
# Tests: _build_child_tool_registry
# ---------------------------------------------------------------------------


class TestBuildChildToolRegistry:
    def test_inherits_all_below_max_depth(self) -> None:
        parent = ToolRegistry()
        parent.register(_FakeTool())
        parent.register(AgentTool())

        child = _build_child_tool_registry(parent, allowed_names=None, child_depth=1, max_depth=3)
        assert "fake" in child
        assert "agent" in child

    def test_removes_agent_at_max_depth(self) -> None:
        parent = ToolRegistry()
        parent.register(_FakeTool())
        parent.register(AgentTool())

        child = _build_child_tool_registry(parent, allowed_names=None, child_depth=3, max_depth=3)
        assert "fake" in child
        assert "agent" not in child

    def test_filters_by_names(self) -> None:
        parent = ToolRegistry()
        parent.register(_FakeTool())
        parent.register(_FakeGrepTool())
        parent.register(AgentTool())

        child = _build_child_tool_registry(
            parent, allowed_names={"fake", "agent"}, child_depth=1, max_depth=3
        )
        assert "fake" in child
        assert "agent" in child
        assert "grep" not in child


# ---------------------------------------------------------------------------
# Tests: AgentFactory
# ---------------------------------------------------------------------------


class TestAgentFactory:
    def test_can_spawn_below_max(self, tmp_path: Path) -> None:
        parent = _make_parent(tmp_path)
        factory = AgentFactory(parent, AgentRuntimeConfig(max_depth=3), depth=1)
        assert factory.can_spawn is True

    def test_cannot_spawn_at_max(self, tmp_path: Path) -> None:
        parent = _make_parent(tmp_path)
        factory = AgentFactory(parent, AgentRuntimeConfig(max_depth=3), depth=3)
        assert factory.can_spawn is False

    def test_build_child_inherits_provider_registry(self, tmp_path: Path) -> None:
        parent = _make_parent(tmp_path)
        factory = AgentFactory(parent, AgentRuntimeConfig(max_depth=3), depth=0)
        parent.agent_factory = factory

        child = factory.build_child()
        assert child._registry is parent._registry

    def test_build_child_fresh_conversation(self, tmp_path: Path) -> None:
        parent = _make_parent(tmp_path)
        factory = AgentFactory(parent, AgentRuntimeConfig(max_depth=3), depth=0)
        parent.agent_factory = factory

        child = factory.build_child()
        assert child.conversation is not parent.conversation
        assert child.conversation.message_count == 0

    def test_build_child_inherits_config(self, tmp_path: Path) -> None:
        parent = _make_parent(tmp_path)
        factory = AgentFactory(parent, AgentRuntimeConfig(max_depth=3), depth=0)
        parent.agent_factory = factory

        child = factory.build_child()
        assert child._config is parent._config

    def test_build_child_narrowed_permission(self, tmp_path: Path) -> None:
        parent = _make_parent(tmp_path)
        factory = AgentFactory(parent, AgentRuntimeConfig(max_depth=3), depth=0)
        parent.agent_factory = factory

        child = factory.build_child(permission_mode=PermissionMode.PLAN)
        assert child.permission_engine.mode == PermissionMode.PLAN

    def test_build_child_shares_permission_settings(self, tmp_path: Path) -> None:
        parent = _make_parent(tmp_path)
        factory = AgentFactory(parent, AgentRuntimeConfig(max_depth=3), depth=0)
        parent.agent_factory = factory

        child = factory.build_child()
        assert child.permission_engine.settings is parent.permission_engine.settings

    def test_build_child_has_factory_with_incremented_depth(self, tmp_path: Path) -> None:
        parent = _make_parent(tmp_path)
        factory = AgentFactory(parent, AgentRuntimeConfig(max_depth=3), depth=0)
        parent.agent_factory = factory

        child = factory.build_child()
        assert child.agent_factory is not None
        assert child.agent_factory.depth == 1

    def test_child_at_max_depth_has_no_agent_tool(self, tmp_path: Path) -> None:
        parent = _make_parent(tmp_path)
        factory = AgentFactory(parent, AgentRuntimeConfig(max_depth=1), depth=0)
        parent.agent_factory = factory

        child = factory.build_child()
        # Child depth = 1, max_depth = 1 → agent tool removed.
        assert "agent" not in child.tool_executor.tool_registry


class TestAgentToolDefinition:
    def test_agent_tool_attributes(self) -> None:
        tool = AgentTool()
        assert tool.name == "agent"
        assert tool.permission_level == PermissionLevel.PROMPT
        assert tool.concurrency == ConcurrencyHint.PARALLEL
        assert tool.max_result_chars is None

    @pytest.mark.asyncio
    async def test_direct_execute_returns_error(self) -> None:
        """AgentTool.execute() should never be called directly."""
        tool = AgentTool()
        result = await tool.execute({"prompt": "test"}, ToolContext(cwd="/tmp"))
        assert result.is_error is True
        assert "orchestrator" in result.output.lower()
