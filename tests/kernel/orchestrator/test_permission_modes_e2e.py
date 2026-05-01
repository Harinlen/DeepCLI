"""E2E tests for permission modes — accept_edits + auto.

Exercises the full pipeline: ToolExecutor → ToolAuthorizer → Tool execution
with real ToolAuthorizer (not a stub), verifying that mode overrides
correctly auto-allow or fall through to the normal ask flow.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from kernel.llm.types import ToolUseContent
from kernel.orchestrator.tool_executor import ToolExecutor
from kernel.orchestrator.types import OrchestratorDeps, PermissionResponse, ToolKind
from kernel.tool_authz.authorizer import ToolAuthorizer
from kernel.tool_authz.rule_parser import parse_rule
from kernel.tool_authz.types import (
    RuleSource,
)
from kernel.tools.tool import Tool
from kernel.tools.types import (
    PermissionSuggestion,
    ToolCallProgress,
    ToolCallResult,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _EditTool(Tool[dict[str, Any], str]):
    """ToolKind.edit — auto-allowed in accept_edits mode."""

    name = "FakeEdit"
    description = "edit"
    kind = ToolKind.edit

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        return PermissionSuggestion(risk="low", default_decision="allow", reason="safe edit")

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    async def validate_input(self, input: dict[str, Any], ctx: Any) -> None:
        pass

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        from kernel.protocol.interfaces.contracts.text_block import TextBlock
        from kernel.tools.types import TextDisplay

        yield ToolCallResult(
            data={"text": "edited"},
            llm_content=[TextBlock(type="text", text="edited")],
            display=TextDisplay(text="edited"),
        )


class _ExecTool(Tool[dict[str, Any], str]):
    """ToolKind.execute — NOT auto-allowed in accept_edits mode."""

    name = "FakeExec"
    description = "exec"
    kind = ToolKind.execute

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        return PermissionSuggestion(risk="medium", default_decision="ask", reason="exec")

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    async def validate_input(self, input: dict[str, Any], ctx: Any) -> None:
        pass

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        from kernel.protocol.interfaces.contracts.text_block import TextBlock
        from kernel.tools.types import TextDisplay

        yield ToolCallResult(
            data={"text": "executed"},
            llm_content=[TextBlock(type="text", text="executed")],
            display=TextDisplay(text="executed"),
        )


class _LowRiskTool(Tool[dict[str, Any], str]):
    """Low-risk tool — auto-allowed in auto mode when ask rule applies."""

    name = "LowRisk"
    description = "low risk"
    kind = ToolKind.read

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        return PermissionSuggestion(risk="low", default_decision="allow", reason="safe")

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    def prepare_permission_matcher(self, input: dict[str, Any]):  # noqa: ANN201
        return lambda _pattern: True

    async def validate_input(self, input: dict[str, Any], ctx: Any) -> None:
        pass

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        from kernel.protocol.interfaces.contracts.text_block import TextBlock
        from kernel.tools.types import TextDisplay

        yield ToolCallResult(
            data={"text": "done"},
            llm_content=[TextBlock(type="text", text="done")],
            display=TextDisplay(text="done"),
        )


class _HighRiskTool(Tool[dict[str, Any], str]):
    """High-risk tool — NOT auto-allowed in auto mode."""

    name = "HighRisk"
    description = "high risk"
    kind = ToolKind.execute

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        return PermissionSuggestion(risk="high", default_decision="ask", reason="dangerous")

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    def prepare_permission_matcher(self, input: dict[str, Any]):  # noqa: ANN201
        return lambda _pattern: True

    async def validate_input(self, input: dict[str, Any], ctx: Any) -> None:
        pass

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        from kernel.protocol.interfaces.contracts.text_block import TextBlock
        from kernel.tools.types import TextDisplay

        yield ToolCallResult(
            data={"text": "ran"},
            llm_content=[TextBlock(type="text", text="ran")],
            display=TextDisplay(text="ran"),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _real_authorizer() -> ToolAuthorizer:
    """Build a real ToolAuthorizer (bypassing Subsystem.load)."""
    from kernel.tool_authz.bash_classifier import BashClassifier
    from kernel.tool_authz.rule_engine import RuleEngine
    from kernel.tool_authz.rule_store import RuleStore
    from kernel.tool_authz.session_grant_cache import SessionGrantCache

    class _FakeModuleTable:
        config = None
        flags = None

    authz = ToolAuthorizer.__new__(ToolAuthorizer)
    authz._module_table = _FakeModuleTable()  # type: ignore[attr-defined]
    authz._rule_store = RuleStore()
    authz._rule_engine = RuleEngine()
    authz._grant_cache = SessionGrantCache()
    authz._bash_classifier = BashClassifier(enabled=False)
    return authz


def _stub_tool_source(tool: Tool) -> MagicMock:
    src = MagicMock()
    src.lookup.return_value = tool
    src.file_state.return_value = MagicMock()
    return src


def _deps(tool: Tool, authorizer: ToolAuthorizer) -> OrchestratorDeps:
    return OrchestratorDeps(
        provider=MagicMock(),
        tool_source=_stub_tool_source(tool),
        authorizer=authorizer,
    )


def _call(name: str, input: dict[str, Any]) -> ToolUseContent:
    return ToolUseContent(id=f"call-{name}", name=name, input=input)


class _PermissionTracker:
    """Wraps a permission callback and tracks whether it was called."""

    def __init__(self, decision: str = "allow_once") -> None:
        self.called = False
        self._decision = decision

    async def __call__(self, req: Any) -> PermissionResponse:
        self.called = True
        return PermissionResponse(decision=self._decision)


async def _run_with_mode(
    tool: Tool,
    authorizer: ToolAuthorizer,
    mode: str,
    *,
    expect_permission: bool = False,
) -> tuple[list[Any], bool]:
    """Run a single tool call through ToolExecutor with the given mode.

    Returns (events, permission_was_called).
    """
    executor = ToolExecutor(
        _deps(tool, authorizer),
        session_id="s-e2e",
        cwd=Path.cwd(),
    )
    executor.add_tool(_call(tool.name, {"text": "x"}))
    executor.finalize_stream()

    tracker = _PermissionTracker()

    async def _no_permission(req: Any) -> PermissionResponse:
        raise AssertionError(f"Permission callback should not fire, but got request for {req}")

    callback = tracker if expect_permission else _no_permission
    events: list[Any] = []

    async for event, _result in executor.results(
        on_permission=callback,
        mode=mode,
    ):
        events.append(event)

    return events, tracker.called


# ---------------------------------------------------------------------------
# accept_edits E2E
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_accept_edits_auto_allows_edit_tool() -> None:
    """Full pipeline: accept_edits mode + edit tool → no permission prompt → tool runs."""
    authz = _real_authorizer()
    events, perm_called = await _run_with_mode(_EditTool(), authz, "accept_edits")

    event_types = [type(e).__name__ for e in events]
    assert "ToolCallStart" in event_types
    assert "ToolCallResult" in event_types
    assert not perm_called


@pytest.mark.anyio
async def test_accept_edits_asks_for_execute_tool() -> None:
    """Full pipeline: accept_edits mode + execute tool → permission prompt fires."""
    authz = _real_authorizer()
    authz._rule_store._config_rules = [parse_rule("FakeExec", "ask", RuleSource.USER, 0)]

    events, perm_called = await _run_with_mode(
        _ExecTool(), authz, "accept_edits", expect_permission=True
    )

    event_types = [type(e).__name__ for e in events]
    assert "ToolCallStart" in event_types
    assert "ToolCallResult" in event_types
    assert perm_called


# ---------------------------------------------------------------------------
# auto mode E2E
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_auto_mode_allows_low_risk_tool() -> None:
    """Full pipeline: auto mode + low-risk tool with ask rule → no permission prompt."""
    authz = _real_authorizer()
    authz._rule_store._config_rules = [parse_rule("LowRisk", "ask", RuleSource.USER, 0)]

    events, perm_called = await _run_with_mode(_LowRiskTool(), authz, "auto")

    event_types = [type(e).__name__ for e in events]
    assert "ToolCallStart" in event_types
    assert "ToolCallResult" in event_types
    assert not perm_called


@pytest.mark.anyio
async def test_auto_mode_asks_for_high_risk_tool() -> None:
    """Full pipeline: auto mode + high-risk tool → permission prompt fires."""
    authz = _real_authorizer()
    authz._rule_store._config_rules = [parse_rule("HighRisk", "ask", RuleSource.USER, 0)]

    events, perm_called = await _run_with_mode(
        _HighRiskTool(), authz, "auto", expect_permission=True
    )

    event_types = [type(e).__name__ for e in events]
    assert "ToolCallStart" in event_types
    assert "ToolCallResult" in event_types
    assert perm_called


# ---------------------------------------------------------------------------
# Mode switching E2E
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mode_switching_lifecycle() -> None:
    """Verify mode transitions: default → accept_edits → plan → default."""
    from kernel.llm.config import ModelRef
    from kernel.orchestrator import OrchestratorConfig, OrchestratorDeps
    from kernel.orchestrator.orchestrator import StandardOrchestrator

    deps = OrchestratorDeps(provider=MagicMock())
    orc = StandardOrchestrator(
        deps=deps,
        session_id="s-lifecycle",
        config=OrchestratorConfig(model=ModelRef(provider="fake", model="fake"), temperature=None),
    )

    # Default
    assert orc.mode == "default"
    assert orc.plan_mode is False

    # → accept_edits
    orc.set_mode("accept_edits")
    assert orc.mode == "accept_edits"
    assert orc.plan_mode is False

    # → plan
    orc.set_mode("plan")
    assert orc.mode == "plan"
    assert orc.plan_mode is True

    # → default via set_plan_mode backward compat
    orc.set_plan_mode(False)
    assert orc.mode == "default"
    assert orc.plan_mode is False

    # → auto
    orc.set_mode("auto")
    assert orc.mode == "auto"
    assert orc.plan_mode is False

    # → bypass
    orc.set_mode("bypass")
    assert orc.mode == "bypass"
    assert orc.plan_mode is False
