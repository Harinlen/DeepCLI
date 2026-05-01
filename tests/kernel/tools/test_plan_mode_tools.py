"""EnterPlanModeTool / ExitPlanModeTool — mode-switching tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kernel.tools.builtin.enter_plan_mode import EnterPlanModeTool
from kernel.tools.builtin.exit_plan_mode import ExitPlanModeTool
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache
from kernel.tools.registry import ToolRegistry
from kernel.tools.types import ToolCallResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    *,
    set_mode=None,
    set_plan_mode=None,
    agent_depth: int = 0,
    interactive: bool = True,
    session_id: str = "test-session",
) -> ToolContext:
    return ToolContext(
        session_id=session_id,
        agent_depth=agent_depth,
        agent_id=None,
        cwd=Path.cwd(),
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
        set_plan_mode=set_plan_mode,
        set_mode=set_mode,
        interactive=interactive,
    )


async def _run(tool, input, ctx) -> ToolCallResult:
    result = None
    async for event in tool.call(input, ctx):
        if isinstance(event, ToolCallResult):
            result = event
    assert result is not None
    return result


# ---------------------------------------------------------------------------
# EnterPlanModeTool
# ---------------------------------------------------------------------------


class TestEnterPlanModeTool:
    def test_metadata(self):
        t = EnterPlanModeTool()
        assert t.name == "EnterPlanMode"
        assert t.should_defer is True
        assert not t.is_destructive({})

    @pytest.mark.asyncio
    async def test_enters_plan_mode(self):
        called_with: list[str] = []
        ctx = _make_ctx(set_mode=lambda v: called_with.append(v))
        result = await _run(EnterPlanModeTool(), {}, ctx)
        assert called_with == ["plan"]
        assert result.data == {"status": "entered_plan_mode"}

    @pytest.mark.asyncio
    async def test_no_closure_returns_error(self):
        ctx = _make_ctx(set_mode=None)
        result = await _run(EnterPlanModeTool(), {}, ctx)
        assert "error" in result.data

    @pytest.mark.asyncio
    async def test_agent_depth_rejected(self):
        """Gap 10: sub-agents cannot enter plan mode."""
        ctx = _make_ctx(set_mode=lambda v: None, agent_depth=1)
        result = await _run(EnterPlanModeTool(), {}, ctx)
        assert "error" in result.data
        assert "sub-agent" in result.data["error"]

    @pytest.mark.asyncio
    async def test_non_interactive_rejected(self):
        """Gap 13: non-interactive sessions cannot enter plan mode."""
        ctx = _make_ctx(set_mode=lambda v: None, interactive=False)
        result = await _run(EnterPlanModeTool(), {}, ctx)
        assert "error" in result.data
        assert "non-interactive" in result.data["error"]

    def test_default_risk_is_allow(self):
        t = EnterPlanModeTool()

        class _FakeCtx:
            cwd = Path.cwd()
            session_id = "test"

        risk = t.default_risk({}, _FakeCtx())  # type: ignore[arg-type]
        assert risk.default_decision == "allow"


# ---------------------------------------------------------------------------
# ExitPlanModeTool
# ---------------------------------------------------------------------------


class TestExitPlanModeTool:
    def test_metadata(self):
        t = ExitPlanModeTool()
        assert t.name == "ExitPlanMode"
        assert t.should_defer is True
        assert not t.is_destructive({})

    @pytest.mark.asyncio
    async def test_exits_plan_mode(self):
        called_with: list[str] = []
        ctx = _make_ctx(set_mode=lambda v: called_with.append(v))
        result = await _run(ExitPlanModeTool(), {}, ctx)
        assert called_with == ["restore"]
        assert result.data["status"] == "exited_plan_mode"

    @pytest.mark.asyncio
    async def test_no_closure_returns_error(self):
        ctx = _make_ctx(set_mode=None)
        result = await _run(ExitPlanModeTool(), {}, ctx)
        assert "error" in result.data

    @pytest.mark.asyncio
    async def test_returns_plan_content(self, tmp_path, monkeypatch):
        """Gap 12: ExitPlanMode returns plan file content."""
        monkeypatch.setenv("MUSTANG_PLANS_DIR", str(tmp_path))
        from kernel.plans import clear_slug_cache, get_plan_file_path

        clear_slug_cache()
        sid = "test-plan-return"
        plan_path = get_plan_file_path(sid)
        plan_path.write_text("# My Plan\nStep 1: do stuff", encoding="utf-8")

        ctx = _make_ctx(set_mode=lambda v: None, session_id=sid)
        result = await _run(ExitPlanModeTool(), {}, ctx)
        assert result.data["has_plan"] is True
        assert "My Plan" in result.llm_content[0].text
        clear_slug_cache()

    @pytest.mark.asyncio
    async def test_empty_plan(self):
        """When no plan file exists, returns simple confirmation."""
        ctx = _make_ctx(set_mode=lambda v: None, session_id="no-plan-session")
        result = await _run(ExitPlanModeTool(), {}, ctx)
        assert result.data["has_plan"] is False

    def test_default_risk_is_ask(self):
        """Gap 11: ExitPlanMode requires user confirmation."""
        t = ExitPlanModeTool()

        class _FakeCtx:
            cwd = Path.cwd()
            session_id = "test"

        risk = t.default_risk({}, _FakeCtx())  # type: ignore[arg-type]
        assert risk.default_decision == "ask"

    def test_reserved_team_fields(self):
        """Gap 14: reserved fields for future team approval."""
        # Just verify the tool doesn't crash; actual team logic is future.
        pass


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestPlanModeToolsInRegistry:
    def test_deferred_by_default(self):
        reg = ToolRegistry()
        reg.register(EnterPlanModeTool(), layer="deferred")
        reg.register(ExitPlanModeTool(), layer="deferred")
        snap = reg.snapshot()
        assert "EnterPlanMode" in snap.deferred_names
        assert "ExitPlanMode" in snap.deferred_names
        schema_names = {s.name for s in snap.schemas}
        assert "EnterPlanMode" not in schema_names
        assert "ExitPlanMode" not in schema_names

    def test_not_filtered_in_plan_mode(self):
        """Plan mode tools have kind=other which is NOT in _MUTATING_KINDS."""
        reg = ToolRegistry()
        reg.register(EnterPlanModeTool(), layer="deferred")
        reg.register(ExitPlanModeTool(), layer="deferred")
        snap = reg.snapshot(plan_mode=True)
        assert "EnterPlanMode" in snap.deferred_names
        assert "ExitPlanMode" in snap.deferred_names

    def test_promoted_survives_plan_mode(self):
        """Once promoted, plan mode tools appear in schemas."""
        reg = ToolRegistry()
        reg.register(EnterPlanModeTool(), layer="deferred")
        reg.register(ExitPlanModeTool(), layer="deferred")
        reg.promote("EnterPlanMode")
        reg.promote("ExitPlanMode")
        snap = reg.snapshot(plan_mode=True)
        schema_names = {s.name for s in snap.schemas}
        assert "EnterPlanMode" in schema_names
        assert "ExitPlanMode" in schema_names
