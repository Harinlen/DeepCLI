"""ToolRegistry — register / lookup / snapshot semantics."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest

from kernel.orchestrator.types import ToolKind
from kernel.tools.registry import ToolRegistry
from kernel.tools.tool import Tool
from kernel.tools.types import ToolCallProgress, ToolCallResult


class _Fake(Tool[dict[str, Any], str]):
    name = "Alpha"
    description = "fake"
    kind = ToolKind.read

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        yield ToolCallResult(data={}, llm_content=[], display=None)  # type: ignore[arg-type]


class _FakeB(_Fake):
    name = "Beta"


def test_register_and_lookup() -> None:
    reg = ToolRegistry()
    reg.register(_Fake(), layer="core")
    assert reg.lookup("Alpha") is not None
    assert reg.lookup("NotThere") is None


def test_duplicate_registration_raises() -> None:
    reg = ToolRegistry()
    reg.register(_Fake(), layer="core")
    with pytest.raises(ValueError):
        reg.register(_Fake(), layer="core")


def test_lookup_via_alias() -> None:
    class _Aliased(_Fake):
        name = "Primary"
        aliases = ("LegacyName",)

    reg = ToolRegistry()
    reg.register(_Aliased(), layer="core")
    assert reg.lookup("Primary") is not None
    assert reg.lookup("LegacyName") is not None


def test_snapshot_sorts_core_alphabetically() -> None:
    reg = ToolRegistry()
    reg.register(_FakeB(), layer="core")
    reg.register(_Fake(), layer="core")

    snap = reg.snapshot()
    names = [s.name for s in snap.schemas]
    assert names == sorted(names)


def test_snapshot_strips_denied_names() -> None:
    reg = ToolRegistry()
    reg.register(_Fake(), layer="core")
    reg.register(_FakeB(), layer="core")

    snap = reg.snapshot(denied_names={"Beta"})
    names = [s.name for s in snap.schemas]
    assert "Beta" not in names
    assert "Alpha" in names


def test_snapshot_honors_agent_whitelist() -> None:
    reg = ToolRegistry()
    reg.register(_Fake(), layer="core")
    reg.register(_FakeB(), layer="core")

    snap = reg.snapshot(agent_whitelist={"Alpha"})
    names = [s.name for s in snap.schemas]
    assert names == ["Alpha"]


def test_snapshot_in_plan_mode_excludes_write_tools() -> None:
    class _Writer(_Fake):
        name = "Writer"
        kind = ToolKind.edit

    reg = ToolRegistry()
    reg.register(_Fake(), layer="core")  # read
    reg.register(_Writer(), layer="core")  # edit → excluded in plan

    snap = reg.snapshot(plan_mode=True)
    names = [s.name for s in snap.schemas]
    assert "Writer" not in names
    assert "Alpha" in names


def test_orchestrate_kind_survives_plan_mode() -> None:
    """ToolKind.orchestrate is not mutating — must pass plan-mode filter.

    AgentTool uses this kind so it stays visible in plan mode (CC parity).
    """

    class _OrchTool(_Fake):
        name = "Orchestrate"
        kind = ToolKind.orchestrate

    reg = ToolRegistry()
    reg.register(_OrchTool(), layer="core")
    reg.register(_Fake(), layer="core")  # read — also passes

    snap = reg.snapshot(plan_mode=True)
    names = {s.name for s in snap.schemas}
    assert "Orchestrate" in names
    assert "Orchestrate" in snap.lookup


def test_execute_kind_excluded_in_plan_mode() -> None:
    """ToolKind.execute is mutating — must be filtered by plan mode."""

    class _ExecTool(_Fake):
        name = "Executor"
        kind = ToolKind.execute

    reg = ToolRegistry()
    reg.register(_ExecTool(), layer="core")

    snap = reg.snapshot(plan_mode=True)
    names = {s.name for s in snap.schemas}
    assert "Executor" not in names
    assert "Executor" not in snap.lookup


# ---------------------------------------------------------------------------
# promote() + deferred listing
# ---------------------------------------------------------------------------


class _DeferredTool(_Fake):
    name = "DeferredAlpha"
    should_defer = True
    search_hint = "fake deferred tool"


class _DeferredToolB(_Fake):
    name = "DeferredBeta"
    should_defer = True


def test_promote_deferred_to_core() -> None:
    reg = ToolRegistry()
    reg.register(_DeferredTool(), layer="deferred")
    assert reg.promote("DeferredAlpha") is True
    # After promotion, snapshot should include the tool's full schema.
    snap = reg.snapshot()
    names = [s.name for s in snap.schemas]
    assert "DeferredAlpha" in names
    assert "DeferredAlpha" not in snap.deferred_names


def test_promote_already_core_returns_false() -> None:
    reg = ToolRegistry()
    reg.register(_Fake(), layer="core")
    assert reg.promote("Alpha") is False


def test_promote_unknown_name_returns_false() -> None:
    reg = ToolRegistry()
    assert reg.promote("Nonexistent") is False


def test_deferred_listing_format() -> None:
    reg = ToolRegistry()
    reg.register(_DeferredTool(), layer="deferred")
    reg.register(_DeferredToolB(), layer="deferred")

    snap = reg.snapshot()
    assert "DeferredAlpha" in snap.deferred_listing
    assert "DeferredBeta" in snap.deferred_listing
    assert "ToolSearch" in snap.deferred_listing


def test_deferred_listing_empty_when_no_deferred() -> None:
    reg = ToolRegistry()
    reg.register(_Fake(), layer="core")

    snap = reg.snapshot()
    assert snap.deferred_listing == ""
    assert len(snap.deferred_names) == 0


def test_snapshot_deferred_not_in_schemas() -> None:
    """Deferred tools should appear in deferred_names but NOT in schemas."""
    reg = ToolRegistry()
    reg.register(_Fake(), layer="core")
    reg.register(_DeferredTool(), layer="deferred")

    snap = reg.snapshot()
    schema_names = [s.name for s in snap.schemas]
    assert "Alpha" in schema_names
    assert "DeferredAlpha" not in schema_names
    assert "DeferredAlpha" in snap.deferred_names


def test_snapshot_deferred_still_in_lookup() -> None:
    """Deferred tools must be in the lookup dict so ToolExecutor can
    dispatch to them after ToolSearch promotes them mid-conversation."""
    reg = ToolRegistry()
    reg.register(_DeferredTool(), layer="deferred")

    snap = reg.snapshot()
    assert "DeferredAlpha" in snap.lookup
