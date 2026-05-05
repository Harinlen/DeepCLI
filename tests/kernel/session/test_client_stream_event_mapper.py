from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from kernel.orchestrator.events import (
    ConfigOptionChanged,
    ModeChanged,
    QueryError,
    SessionInfoChanged,
    TextDelta,
    ToolCallLocations,
    ToolCallResult,
    ToolCallStart,
)
from kernel.orchestrator.tool_kinds import ToolKind
from kernel.protocol.acp.schemas.updates import (
    AgentMessageChunk,
    ConfigOptionUpdate,
    CurrentModeUpdate,
    SessionInfoUpdate,
    ToolCallLocation,
    ToolCallStart as AcpToolCallStart,
    ToolCallUpdateNotification,
)
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.session.client_stream.event_mapper import SessionEventMapperMixin
from kernel.session.events import (
    ConfigOptionChangedEvent,
    ModeChangedEvent,
    SessionInfoChangedEvent,
    ToolCallEvent,
    ToolCallUpdateEvent,
)
from kernel.session.runtime.state import Session


@dataclass
class _Record:
    title_source: str | None = None


class _Store:
    def __init__(self, record: _Record | None = None) -> None:
        self.record = record
        self.title_updates: list[tuple[str, str, str]] = []

    async def get_session(self, session_id: str) -> _Record | None:
        return self.record

    async def update_title(self, session_id: str, title: str, *, title_source: str) -> None:
        self.title_updates.append((session_id, title, title_source))


class _Mapper(SessionEventMapperMixin):
    def __init__(self, *, store: _Store | None = None) -> None:
        self._store = store or _Store()
        self.writes: list[tuple[type, dict[str, Any]]] = []
        self.broadcasts: list[Any] = []
        self.spilled: list[tuple[str, list[dict[str, Any]]]] = []

    async def _write_event(self, session: Session, event_cls: type, **kwargs: Any) -> str:
        self.writes.append((event_cls, kwargs))
        return f"event-{len(self.writes)}"

    async def _broadcast(self, session: Session, update: Any) -> None:
        self.broadcasts.append(update)

    @staticmethod
    def _blocks_to_raw(blocks: list[Any]) -> list[dict[str, Any]]:
        return [block.model_dump() if hasattr(block, "model_dump") else block for block in blocks]

    def _maybe_spill(
        self,
        session: Session,
        tool_call_id: str,
        content_raw: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        self.spilled.append((tool_call_id, content_raw))
        return [{"type": "text", "text": f"spilled:{tool_call_id}"}]


def _session() -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        session_id="s-1",
        cwd=Path.cwd(),
        created_at=now,
        updated_at=now,
        title=None,
        git_branch=None,
        mode_id="default",
        config_options={},
        mcp_servers=[],
        orchestrator=None,  # type: ignore[arg-type]
    )


@pytest.mark.anyio
async def test_text_delta_is_accumulated_and_broadcast_as_agent_message_chunk() -> None:
    mapper = _Mapper()
    session = _session()
    accumulated_text: list[str] = []
    accumulated_thought: list[str] = []

    await mapper._handle_orchestrator_event(
        session,
        TextDelta(content="hello"),
        accumulated_text,
        accumulated_thought,
    )

    assert accumulated_text == ["hello"]
    assert accumulated_thought == []
    assert len(mapper.broadcasts) == 1
    update = mapper.broadcasts[0]
    assert isinstance(update, AgentMessageChunk)
    assert update.content.text == "hello"
    assert mapper.writes == []


@pytest.mark.anyio
async def test_query_error_is_accumulated_and_broadcast_as_error_text() -> None:
    mapper = _Mapper()
    session = _session()
    accumulated_text: list[str] = []
    accumulated_thought: list[str] = []

    await mapper._handle_orchestrator_event(
        session,
        QueryError(message="connection dropped", code="transient_transport"),
        accumulated_text,
        accumulated_thought,
    )

    assert accumulated_text == ["Error: connection dropped"]
    assert accumulated_thought == []
    assert len(mapper.broadcasts) == 1
    update = mapper.broadcasts[0]
    assert isinstance(update, AgentMessageChunk)
    assert update.content.text == "Error: connection dropped"
    assert update.meta == {"mustang.agent/errorCode": "transient_transport"}
    assert mapper.writes == []


@pytest.mark.anyio
async def test_tool_start_persists_and_broadcasts_pending_tool_call() -> None:
    mapper = _Mapper()
    session = _session()

    await mapper._handle_orchestrator_event(
        session,
        ToolCallStart(
            id="tool-1",
            title="Delegate work",
            kind=ToolKind.orchestrate,
            raw_input='{"task":"inspect"}',
        ),
        [],
        [],
    )

    assert mapper.writes == [
        (
            ToolCallEvent,
            {
                "tool_call_id": "tool-1",
                "title": "Delegate work",
                "kind": "other",
                "raw_input": '{"task":"inspect"}',
            },
        )
    ]
    update = mapper.broadcasts[0]
    assert isinstance(update, AcpToolCallStart)
    assert update.tool_call_id == "tool-1"
    assert update.kind == "other"
    assert update.raw_input == '{"task":"inspect"}'


@pytest.mark.anyio
async def test_tool_result_spills_persisted_content_but_broadcasts_inline_content() -> None:
    mapper = _Mapper()
    session = _session()

    await mapper._handle_orchestrator_event(
        session,
        ToolCallResult(id="tool-1", content=[TextBlock(text="visible result")]),
        [],
        [],
    )

    assert mapper.spilled == [("tool-1", [{"type": "text", "text": "visible result"}])]
    assert mapper.writes == [
        (
            ToolCallUpdateEvent,
            {
                "tool_call_id": "tool-1",
                "status": "completed",
                "content": [{"type": "text", "text": "spilled:tool-1"}],
            },
        )
    ]
    update = mapper.broadcasts[0]
    assert isinstance(update, ToolCallUpdateNotification)
    assert update.tool_call_id == "tool-1"
    assert update.status == "completed"
    assert update.content == [{"type": "text", "text": "visible result"}]


@pytest.mark.anyio
async def test_tool_result_broadcasts_meta_without_persisting_it() -> None:
    mapper = _Mapper()
    session = _session()

    await mapper._handle_orchestrator_event(
        session,
        ToolCallResult(
            id="agent-1",
            content=[TextBlock(text="agent result")],
            meta={
                "mustang.agent/agentStats": {
                    "toolUseCount": 2,
                    "totalTokens": 1500,
                    "durationMs": 13000,
                }
            },
        ),
        [],
        [],
    )

    assert mapper.writes == [
        (
            ToolCallUpdateEvent,
            {
                "tool_call_id": "agent-1",
                "status": "completed",
                "content": [{"type": "text", "text": "spilled:agent-1"}],
            },
        )
    ]
    update = mapper.broadcasts[0]
    assert isinstance(update, ToolCallUpdateNotification)
    assert update.meta == {
        "mustang.agent/agentStats": {
            "toolUseCount": 2,
            "totalTokens": 1500,
            "durationMs": 13000,
        }
    }


@pytest.mark.anyio
async def test_tool_locations_are_broadcast_without_persisting_an_event() -> None:
    mapper = _Mapper()
    session = _session()

    await mapper._handle_orchestrator_event(
        session,
        ToolCallLocations(
            id="tool-1",
            locations=[
                {"path": "src/kernel/kernel/app.py", "line": 10},
                {"path": "README.md"},
            ],
        ),
        [],
        [],
    )

    assert mapper.writes == []
    update = mapper.broadcasts[0]
    assert isinstance(update, ToolCallUpdateNotification)
    assert update.locations == [
        ToolCallLocation(path="src/kernel/kernel/app.py", line=10),
        ToolCallLocation(path="README.md", line=None),
    ]


@pytest.mark.anyio
async def test_mode_change_updates_session_persists_old_mode_and_broadcasts_current_mode() -> None:
    mapper = _Mapper()
    session = _session()

    await mapper._handle_orchestrator_event(session, ModeChanged(mode_id="plan"), [], [])

    assert session.mode_id == "plan"
    assert mapper.writes == [(ModeChangedEvent, {"mode_id": "plan", "from_mode": "default"})]
    update = mapper.broadcasts[0]
    assert isinstance(update, CurrentModeUpdate)
    assert update.mode_id == "plan"


@pytest.mark.anyio
async def test_config_options_update_session_persist_snapshot_and_broadcast_descriptors() -> None:
    mapper = _Mapper()
    session = _session()

    await mapper._handle_orchestrator_event(
        session,
        ConfigOptionChanged(options={"mode": "plan"}),
        [],
        [],
    )

    assert session.config_options == {"mode": "plan"}
    assert mapper.writes == [
        (
            ConfigOptionChangedEvent,
            {
                "config_id": "",
                "value": "",
                "full_state": {"mode": "plan"},
            },
        )
    ]
    update = mapper.broadcasts[0]
    assert isinstance(update, ConfigOptionUpdate)
    by_id = {item["configId"]: item for item in update.config_options}
    assert by_id["mode"]["currentValue"] == "plan"
    assert {option["value"] for option in by_id["mode"]["options"]} >= {"default", "plan"}


@pytest.mark.anyio
async def test_session_info_auto_title_is_ignored_when_user_title_exists() -> None:
    mapper = _Mapper(store=_Store(_Record(title_source="user")))
    session = _session()

    await mapper._handle_orchestrator_event(
        session,
        SessionInfoChanged(title="auto title"),
        [],
        [],
    )

    assert session.title is None
    assert mapper.writes == []
    assert mapper.broadcasts == []


@pytest.mark.anyio
async def test_session_info_update_persists_and_broadcasts_when_no_user_title_exists() -> None:
    mapper = _Mapper(store=_Store(None))
    session = _session()

    await mapper._handle_orchestrator_event(
        session,
        SessionInfoChanged(title="auto title"),
        [],
        [],
    )

    assert session.title == "auto title"
    assert mapper.writes == [(SessionInfoChangedEvent, {"title": "auto title"})]
    update = mapper.broadcasts[0]
    assert isinstance(update, SessionInfoUpdate)
    assert update.title == "auto title"
