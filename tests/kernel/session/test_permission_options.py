"""Session permission requests honor dynamic orchestrator options."""

from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from kernel.llm.types import ToolUseContent
from kernel.orchestrator.events import ToolCallResult as ToolCallResultEvent
from kernel.orchestrator.tool_executor import ToolExecutor
from kernel.orchestrator.types import (
    OrchestratorDeps,
    PermissionRequest,
    PermissionRequestOption,
    ToolKind,
)
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.session.runtime.state import Session
from kernel.session.turns.permission import SessionPermissionMixin
from kernel.tool_authz.types import PermissionAsk, PermissionSuggestionBtn, ReasonDefaultRisk
from kernel.tools.tool import Tool
from kernel.tools.types import PermissionSuggestion, TextDisplay, ToolCallProgress, ToolCallResult


class _Sender:
    """Capture the outgoing ACP permission request."""

    def __init__(self) -> None:
        self.method: str | None = None
        self.params: Any = None

    async def request(self, method: str, params: Any, *, result_type: Any) -> Any:
        self.method = method
        self.params = params
        return result_type.model_validate(
            {"outcome": {"outcome": "selected", "optionId": "allow_once"}}
        )


class _Harness(SessionPermissionMixin):
    """Minimal concrete mixin host for exercising _on_permission."""

    def __init__(self) -> None:
        self.events: list[tuple[type, dict[str, Any]]] = []

    async def _write_event(self, session: Session, event_cls: type, **kwargs: Any) -> str:
        self.events.append((event_cls, kwargs))
        return f"event-{len(self.events)}"


class _ProbeTool(Tool[dict[str, Any], str]):
    """Small executable tool for the integrated permission seam test."""

    name = "ProbeExec"
    description = "probe"
    kind = ToolKind.execute

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        return PermissionSuggestion(risk="high", default_decision="ask", reason="probe")

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return True

    async def validate_input(self, input: dict[str, Any], ctx: Any) -> None:
        pass

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        yield ToolCallResult(
            data="ok",
            llm_content=[TextBlock(type="text", text="ok")],
            display=TextDisplay(text="ok"),
        )


class _AskNoAlwaysAuthorizer:
    """Authorizer returning a destructive-style ask without allow_always."""

    async def authorize(self, **_: Any) -> PermissionAsk:
        return PermissionAsk(
            message="approve probe?",
            decision_reason=ReasonDefaultRisk(
                risk="high", reason="destructive", tool_name="ProbeExec"
            ),
            suggestions=[
                PermissionSuggestionBtn(label="Allow once", outcome="allow_once"),
                PermissionSuggestionBtn(label="Deny", outcome="deny"),
            ],
        )

    def grant(self, **_: Any) -> None:
        pass


class _ToolSource:
    """Minimal ToolManager-like source for ToolExecutor."""

    def __init__(self, tool: Tool[dict[str, Any], Any]) -> None:
        self._tool = tool

    def lookup(self, name: str) -> Tool[dict[str, Any], Any] | None:
        return self._tool if name == self._tool.name else None

    def file_state(self, *_: Any) -> None:
        return None


def _session(sender: _Sender) -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        session_id="s-1",
        cwd=Path.cwd(),
        created_at=now,
        updated_at=now,
        title=None,
        git_branch=None,
        mode_id=None,
        config_options={},
        mcp_servers=[],
        orchestrator=None,  # type: ignore[arg-type]
        senders={"c-1": sender},  # type: ignore[dict-item]
    )


@pytest.mark.anyio
async def test_session_permission_uses_request_options() -> None:
    sender = _Sender()
    req = PermissionRequest(
        tool_use_id="tool-1",
        tool_name="Dangerous",
        tool_title="Dangerous",
        input_summary="destructive action",
        risk_level="high",
        options=(
            PermissionRequestOption(
                option_id="allow_once",
                name="Allow once",
                kind="allow_once",
            ),
            PermissionRequestOption(
                option_id="reject",
                name="Deny",
                kind="reject_once",
            ),
        ),
    )

    result = await _Harness()._on_permission(_session(sender), req)

    assert result.decision == "allow_once"
    assert sender.method == "session/request_permission"
    assert sender.params is not None
    option_ids = [option.option_id for option in sender.params.options]
    assert option_ids == ["allow_once", "reject"]
    assert "allow_always" not in option_ids


@pytest.mark.anyio
async def test_session_permission_keeps_legacy_defaults_when_options_empty() -> None:
    sender = _Sender()
    req = PermissionRequest(
        tool_use_id="tool-1",
        tool_name="Bash",
        tool_title="Bash",
        input_summary="run command",
        risk_level="medium",
    )

    result = await _Harness()._on_permission(_session(sender), req)

    assert result.decision == "allow_once"
    assert sender.params is not None
    option_ids = [option.option_id for option in sender.params.options]
    assert option_ids == ["allow_once", "allow_always", "reject"]


@pytest.mark.anyio
async def test_tool_executor_session_permission_options_integration() -> None:
    """Real ToolExecutor + SessionPermissionMixin preserve dynamic options."""
    sender = _Sender()
    harness = _Harness()
    session = _session(sender)
    tool = _ProbeTool()
    executor = ToolExecutor(
        OrchestratorDeps(
            provider=None,  # type: ignore[arg-type]
            tool_source=_ToolSource(tool),  # type: ignore[arg-type]
            authorizer=_AskNoAlwaysAuthorizer(),  # type: ignore[arg-type]
        ),
        session_id=session.session_id,
        cwd=session.cwd,
    )
    executor.add_tool(ToolUseContent(id="tool-1", name=tool.name, input={}))
    executor.finalize_stream()

    events = []
    async for event, _result in executor.results(
        on_permission=lambda req: harness._on_permission(session, req),
        mode="default",
    ):
        events.append(event)

    option_ids = [option.option_id for option in sender.params.options]
    assert option_ids == ["allow_once", "reject"]
    assert "allow_always" not in option_ids
    assert any(isinstance(event, ToolCallResultEvent) for event in events)
