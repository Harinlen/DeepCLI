"""Unit tests for AcpEventMapper — orchestrator event → session/update."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any
from unittest.mock import AsyncMock

import pytest

from kernel.protocol.acp.event_mapper import AcpEventMapper


# ---------------------------------------------------------------------------
# Lightweight stubs for orchestrator types — avoids coupling tests to the
# full orchestrator module while matching the real dataclass shapes.
# ---------------------------------------------------------------------------


class _ToolKind(str, Enum):
    read = "read"
    edit = "edit"
    execute = "execute"
    search = "search"
    other = "other"


class _StopReason(str, Enum):
    end_turn = "end_turn"
    cancelled = "cancelled"
    error = "error"


# We import the real event types to test with actual classes.
from kernel.orchestrator.events import (  # noqa: E402
    AvailableCommandsChanged,
    CancelledEvent,
    CompactionEvent,
    ConfigOptionChanged,
    ModeChanged,
    PlanUpdate,
    QueryError,
    SessionInfoChanged,
    SubAgentEnd,
    SubAgentStart,
    TextDelta,
    ThoughtDelta,
    ToolCallDiff,
    ToolCallError,
    ToolCallLocations,
    ToolCallProgress,
    ToolCallResult,
    ToolCallStart,
    UserPromptBlocked,
)
from kernel.orchestrator.types import StopReason, ToolKind  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal ContentBlock stubs (match protocol-neutral contract shapes)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TextBlock:
    type: str = "text"
    text: str = ""


@dataclass(frozen=True)
class _ImageBlock:
    type: str = "image"
    data: str = ""
    mime_type: str = "image/png"


@dataclass(frozen=True)
class _ResourceLinkBlock:
    type: str = "resource_link"
    uri: str = ""
    mime_type: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class _ResourceBlock:
    type: str = "resource"
    uri: str = ""
    mime_type: str | None = None
    text: str | None = None
    blob: str | None = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SESSION_ID = "test-session-001"


@pytest.fixture
def mapper() -> AcpEventMapper:
    return AcpEventMapper()


@pytest.fixture
def sender() -> AsyncMock:
    return AsyncMock()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _get_update(sender: AsyncMock) -> dict[str, Any]:
    """Extract the session/update notification from the mock sender."""
    sender.notify.assert_called_once()
    args = sender.notify.call_args
    assert args[0][0] == "session/update"
    notif = args[0][1]
    return notif


# ---------------------------------------------------------------------------
# Text / thought streaming
# ---------------------------------------------------------------------------


class TestTextDelta:
    @pytest.mark.asyncio
    async def test_text_delta(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        await mapper.map(TextDelta(content="hello"), sender, SESSION_ID)
        notif = _get_update(sender)
        assert notif.session_id == SESSION_ID
        assert notif.update.session_update == "agent_message_chunk"
        assert notif.update.content.type == "text"
        assert notif.update.content.text == "hello"

    @pytest.mark.asyncio
    async def test_thought_delta(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        await mapper.map(ThoughtDelta(content="thinking..."), sender, SESSION_ID)
        notif = _get_update(sender)
        assert notif.update.session_update == "agent_thought_chunk"
        assert notif.update.content.text == "thinking..."


# ---------------------------------------------------------------------------
# Tool call lifecycle
# ---------------------------------------------------------------------------


class TestToolCallLifecycle:
    @pytest.mark.asyncio
    async def test_tool_call_start(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        event = ToolCallStart(
            id="tc-1",
            title="Read file",
            kind=ToolKind.read,
            raw_input='{"path": "/tmp/x"}',
        )
        await mapper.map(event, sender, SESSION_ID)
        notif = _get_update(sender)
        update = notif.update
        assert update.session_update == "tool_call"
        assert update.tool_call_id == "tc-1"
        assert update.title == "Read file"
        assert update.kind == "read"
        assert update.status == "pending"
        assert update.raw_input == '{"path": "/tmp/x"}'

    @pytest.mark.asyncio
    async def test_tool_call_progress(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        event = ToolCallProgress(
            id="tc-1",
            content=[_TextBlock(text="partial output")],
        )
        await mapper.map(event, sender, SESSION_ID)
        notif = _get_update(sender)
        update = notif.update
        assert update.session_update == "tool_call_update"
        assert update.tool_call_id == "tc-1"
        assert update.status == "in_progress"
        assert update.content == [{"type": "text", "text": "partial output"}]

    @pytest.mark.asyncio
    async def test_tool_call_result(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        event = ToolCallResult(
            id="tc-1",
            content=[_TextBlock(text="done")],
            meta={"mustang.agent/agentStats": {"totalTokens": 42}},
        )
        await mapper.map(event, sender, SESSION_ID)
        notif = _get_update(sender)
        update = notif.update
        assert update.status == "completed"
        assert update.content == [{"type": "text", "text": "done"}]
        assert update.meta == {"mustang.agent/agentStats": {"totalTokens": 42}}

    @pytest.mark.asyncio
    async def test_tool_call_error(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        event = ToolCallError(id="tc-1", error="permission denied")
        await mapper.map(event, sender, SESSION_ID)
        notif = _get_update(sender)
        update = notif.update
        assert update.status == "failed"
        assert update.content == [{"type": "text", "text": "permission denied"}]

    @pytest.mark.asyncio
    async def test_tool_call_diff(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        event = ToolCallDiff(
            id="tc-1",
            path="/src/main.py",
            old_text="old code",
            new_text="new code",
        )
        await mapper.map(event, sender, SESSION_ID)
        notif = _get_update(sender)
        update = notif.update
        assert update.status == "completed"
        assert len(update.content) == 1
        diff = update.content[0]
        assert diff["type"] == "diff"
        assert diff["path"] == "/src/main.py"
        assert diff["oldText"] == "old code"
        assert diff["newText"] == "new code"

    @pytest.mark.asyncio
    async def test_tool_call_diff_new_file(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        event = ToolCallDiff(id="tc-1", path="/new.py", old_text=None, new_text="content")
        await mapper.map(event, sender, SESSION_ID)
        notif = _get_update(sender)
        assert notif.update.content[0]["oldText"] is None

    @pytest.mark.asyncio
    async def test_tool_call_locations(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        event = ToolCallLocations(
            id="tc-1",
            locations=[
                {"path": "/src/a.py", "line": 42},
                {"path": "/src/b.py"},
            ],
        )
        await mapper.map(event, sender, SESSION_ID)
        notif = _get_update(sender)
        update = notif.update
        assert update.status == "completed"
        assert len(update.locations) == 2
        assert update.locations[0].path == "/src/a.py"
        assert update.locations[0].line == 42
        assert update.locations[1].path == "/src/b.py"
        assert update.locations[1].line is None


# ---------------------------------------------------------------------------
# Session / UI state
# ---------------------------------------------------------------------------


class TestSessionState:
    @pytest.mark.asyncio
    async def test_plan_update(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        event = PlanUpdate(
            entries=[
                {"title": "Step 1", "priority": "high", "status": "completed"},
                {"title": "Step 2", "status": "pending"},
            ],
        )
        await mapper.map(event, sender, SESSION_ID)
        notif = _get_update(sender)
        update = notif.update
        assert update.session_update == "plan"
        assert len(update.entries) == 2
        assert update.entries[0].content == "Step 1"
        assert update.entries[0].priority == "high"
        assert update.entries[0].status == "completed"
        assert update.entries[1].content == "Step 2"
        assert update.entries[1].priority == "medium"  # default

    @pytest.mark.asyncio
    async def test_mode_changed(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        await mapper.map(ModeChanged(mode_id="plan"), sender, SESSION_ID)
        notif = _get_update(sender)
        assert notif.update.session_update == "current_mode_update"
        assert notif.update.mode_id == "plan"

    @pytest.mark.asyncio
    async def test_config_option_changed(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        event = ConfigOptionChanged(options={"mode": "plan"})
        await mapper.map(event, sender, SESSION_ID)
        notif = _get_update(sender)
        update = notif.update
        assert update.session_update == "config_option_update"
        assert update.config_options[0]["configId"] == "mode"
        assert update.config_options[0]["name"] == "Mode"
        assert update.config_options[0]["currentValue"] == "plan"
        assert {option["value"] for option in update.config_options[0]["options"]} >= {
            "default",
            "plan",
            "bypass",
            "accept_edits",
            "auto",
            "dont_ask",
        }

    @pytest.mark.asyncio
    async def test_session_info_changed(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        await mapper.map(SessionInfoChanged(title="New title"), sender, SESSION_ID)
        notif = _get_update(sender)
        assert notif.update.session_update == "session_info_update"
        assert notif.update.title == "New title"

    @pytest.mark.asyncio
    async def test_available_commands_changed(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        cmds = [{"name": "/help", "description": "Show help"}]
        await mapper.map(AvailableCommandsChanged(commands=cmds), sender, SESSION_ID)
        notif = _get_update(sender)
        assert notif.update.session_update == "available_commands_update"
        assert notif.update.available_commands == cmds


# ---------------------------------------------------------------------------
# Sub-agent bracketing
# ---------------------------------------------------------------------------


class TestSubAgent:
    @pytest.mark.asyncio
    async def test_sub_agent_start(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        event = SubAgentStart(
            agent_id="agent-123",
            description="Explore codebase",
            agent_type="Explore",
            spawned_by_tool_id="tc-agent-1",
        )
        await mapper.map(event, sender, SESSION_ID)
        notif = _get_update(sender)
        assert notif.update.tool_call_id == "tc-agent-1"
        assert notif.update.status == "in_progress"
        assert notif.meta["mustang.agent/agentStart"]["agent_id"] == "agent-123"
        assert notif.meta["mustang.agent/agentStart"]["agent_type"] == "Explore"

    @pytest.mark.asyncio
    async def test_sub_agent_end_uses_tracked_tool_id(
        self, mapper: AcpEventMapper, sender: AsyncMock,
    ) -> None:
        # First emit start to register the mapping.
        start = SubAgentStart(
            agent_id="agent-123",
            description="Explore",
            agent_type="Explore",
            spawned_by_tool_id="tc-agent-1",
        )
        await mapper.map(start, sender, SESSION_ID)
        sender.reset_mock()

        # Now emit end — should use the tracked tool_call_id.
        end = SubAgentEnd(
            agent_id="agent-123",
            stop_reason=StopReason.end_turn,
            input_tokens=1000,
            output_tokens=500,
            tool_use_count=3,
            duration_ms=12000,
        )
        await mapper.map(end, sender, SESSION_ID)
        notif = _get_update(sender)
        assert notif.update.tool_call_id == "tc-agent-1"
        assert notif.meta["mustang.agent/agentEnd"]["agent_id"] == "agent-123"
        assert notif.meta["mustang.agent/agentEnd"]["stop_reason"] == "end_turn"
        assert notif.meta["mustang.agent/agentEnd"]["toolUseCount"] == 3
        assert notif.meta["mustang.agent/agentEnd"]["totalTokens"] == 1500
        assert notif.meta["mustang.agent/agentEnd"]["durationMs"] == 12000

    @pytest.mark.asyncio
    async def test_sub_agent_end_fallback_to_agent_id(
        self, mapper: AcpEventMapper, sender: AsyncMock,
    ) -> None:
        """If SubAgentStart was somehow missed, fall back to agent_id."""
        end = SubAgentEnd(
            agent_id="agent-orphan",
            stop_reason=StopReason.error,
        )
        await mapper.map(end, sender, SESSION_ID)
        notif = _get_update(sender)
        assert notif.update.tool_call_id == "agent-orphan"

    @pytest.mark.asyncio
    async def test_sub_agent_tracking_cleaned_up(
        self, mapper: AcpEventMapper, sender: AsyncMock,
    ) -> None:
        """After SubAgentEnd, the tracking entry is removed."""
        start = SubAgentStart(
            agent_id="agent-123",
            description="x",
            agent_type="x",
            spawned_by_tool_id="tc-1",
        )
        end = SubAgentEnd(agent_id="agent-123", stop_reason=StopReason.end_turn)
        await mapper.map(start, sender, SESSION_ID)
        sender.reset_mock()
        await mapper.map(end, sender, SESSION_ID)
        # Mapping should be cleaned up.
        assert "agent-123" not in mapper._agent_tool_ids


# ---------------------------------------------------------------------------
# Housekeeping — not sent to client
# ---------------------------------------------------------------------------


class TestHousekeeping:
    @pytest.mark.asyncio
    async def test_compaction_not_sent(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        await mapper.map(CompactionEvent(tokens_before=5000, tokens_after=2000), sender, SESSION_ID)
        sender.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_query_error_not_sent(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        await mapper.map(QueryError(message="rate limit"), sender, SESSION_ID)
        sender.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_prompt_blocked_not_sent(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        await mapper.map(UserPromptBlocked(reason="hook block"), sender, SESSION_ID)
        sender.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancelled_not_sent(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        await mapper.map(CancelledEvent(), sender, SESSION_ID)
        sender.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_event_not_sent(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        await mapper.map({"totally": "unknown"}, sender, SESSION_ID)
        sender.notify.assert_not_called()


# ---------------------------------------------------------------------------
# Content block conversion
# ---------------------------------------------------------------------------


class TestContentBlockConversion:
    @pytest.mark.asyncio
    async def test_image_content_block(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        event = ToolCallResult(
            id="tc-1",
            content=[_ImageBlock(data="base64data", mime_type="image/png")],
        )
        await mapper.map(event, sender, SESSION_ID)
        notif = _get_update(sender)
        block = notif.update.content[0]
        assert block["type"] == "image"
        assert block["data"] == "base64data"
        assert block["mime_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_resource_link_content_block(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        event = ToolCallResult(
            id="tc-1",
            content=[_ResourceLinkBlock(uri="file:///tmp/x", name="x.txt")],
        )
        await mapper.map(event, sender, SESSION_ID)
        notif = _get_update(sender)
        block = notif.update.content[0]
        assert block["type"] == "resource_link"
        assert block["uri"] == "file:///tmp/x"
        assert block["name"] == "x.txt"

    @pytest.mark.asyncio
    async def test_resource_content_block(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        event = ToolCallResult(
            id="tc-1",
            content=[_ResourceBlock(uri="file:///tmp/x", text="file content")],
        )
        await mapper.map(event, sender, SESSION_ID)
        notif = _get_update(sender)
        block = notif.update.content[0]
        assert block["type"] == "resource"
        assert block["resource"]["uri"] == "file:///tmp/x"
        assert block["resource"]["text"] == "file content"

    @pytest.mark.asyncio
    async def test_multiple_content_blocks(self, mapper: AcpEventMapper, sender: AsyncMock) -> None:
        event = ToolCallResult(
            id="tc-1",
            content=[
                _TextBlock(text="line 1"),
                _TextBlock(text="line 2"),
            ],
        )
        await mapper.map(event, sender, SESSION_ID)
        notif = _get_update(sender)
        assert len(notif.update.content) == 2
        assert notif.update.content[0]["text"] == "line 1"
        assert notif.update.content[1]["text"] == "line 2"
