"""ACP implementation of EventMapper — Orchestrator event → session/update.

Mapping table (see ``docs/kernel/interfaces/protocol.md`` §会话层事件):

``TextDelta``                  → ``agent_message_chunk`` (type: text)
``ThoughtDelta``               → ``agent_thought_chunk``  (type: text)
``PlanUpdate``                 → ``plan``
``ToolCallStart``              → ``tool_call``            (status: pending)
``ToolCallProgress``           → ``tool_call_update``     (status: in_progress)
``ToolCallResult``             → ``tool_call_update``     (status: completed)
``ToolCallError``              → ``tool_call_update``     (status: failed)
``ToolCallDiff``               → ``tool_call_update``     (content: diff block)
``ToolCallLocations``          → ``tool_call_update``     (locations field)
``ModeChanged``                → ``current_mode_update``
``ConfigOptionChanged``        → ``config_option_update`` (full config state)
``SessionInfoChanged``         → ``session_info_update``  (partial)
``AvailableCommandsChanged``   → ``available_commands_update``
``SubAgentStart``              → ``tool_call_update`` + ``_meta: mustang.agent/agentStart``
``SubAgentEnd``                → ``tool_call_update`` + ``_meta: mustang.agent/agentEnd``
``PermissionRequest``          → NOT mapped here (session/request_permission request)
``UserMessageEcho``            → NOT mapped here (session/load sends directly)
``CompactionEvent``            → not sent to client
``QueryError``                 → not sent to client (session handler returns error)
``UserPromptBlocked``          → not sent to client (session handler handles)
``CancelledEvent``             → not sent to client (session handler handles)
"""

from __future__ import annotations

import logging
from typing import Any, cast

from kernel.orchestrator.events import (
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
from kernel.protocol.acp.schemas.content import (
    AcpEmbeddedResource,
    AcpImageBlock,
    AcpResourceBlock,
    AcpResourceLinkBlock,
    AcpTextBlock,
)
from kernel.protocol.acp.schemas.enums import AcpToolKind
from kernel.protocol.acp.schemas.updates import (
    AgentMessageChunk,
    AgentThoughtChunk,
    AvailableCommandsUpdate,
    ConfigOptionUpdate,
    CurrentModeUpdate,
    PlanEntry,
)
from kernel.session.runtime.helpers import config_list as _config_list
from kernel.protocol.acp.schemas.updates import (
    PlanUpdate as AcpPlanUpdate,
)
from kernel.protocol.acp.schemas.updates import (
    SessionInfoUpdate,
    SessionUpdateNotification,
    ToolCallLocation,
)
from kernel.protocol.acp.schemas.updates import (
    ToolCallStart as AcpToolCallStart,
)
from kernel.protocol.acp.schemas.updates import (
    ToolCallUpdateNotification,
)
from kernel.protocol.interfaces.client_sender import ClientSender

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Content-block conversion helpers
# ---------------------------------------------------------------------------


def _to_acp_content_block(
    block: Any,
) -> AcpTextBlock | AcpImageBlock | AcpResourceLinkBlock | AcpResourceBlock:
    """Convert a protocol-neutral ContentBlock to its ACP wire-format model.

    Uses duck typing on ``block.type`` so the ACP layer does not import
    protocol-neutral contract types directly.
    """
    block_type = getattr(block, "type", None)
    if block_type == "text":
        return AcpTextBlock(text=block.text)
    if block_type == "image":
        return AcpImageBlock(data=block.data, mime_type=block.mime_type)
    if block_type == "resource_link":
        return AcpResourceLinkBlock(
            uri=block.uri,
            mime_type=getattr(block, "mime_type", None),
            name=getattr(block, "name", None),
        )
    if block_type == "resource":
        return AcpResourceBlock(
            resource=AcpEmbeddedResource(
                uri=block.uri,
                mime_type=getattr(block, "mime_type", None),
                text=getattr(block, "text", None),
                blob=getattr(block, "blob", None),
            ),
        )
    # Fallback: coerce to text.
    return AcpTextBlock(text=str(block))


def _content_blocks_to_dicts(blocks: list[Any]) -> list[dict[str, Any]]:
    """Convert a list of protocol-neutral ContentBlocks to ACP dicts."""
    return [
        _to_acp_content_block(b).model_dump(exclude_none=True) for b in blocks
    ]


# ---------------------------------------------------------------------------
# AcpEventMapper
# ---------------------------------------------------------------------------


class AcpEventMapper:
    """Concrete :class:`~kernel.protocol.interfaces.event_mapper.EventMapper`
    for the ACP protocol stack.

    Translates each ``OrchestratorEvent`` into one ``session/update``
    notification sent via ``sender.notify()``.

    Maintains a small piece of state (``_agent_tool_ids``) to track the
    mapping from sub-agent ``agent_id`` back to the ``ToolCallStart.id``
    of the ``AgentTool`` invocation that spawned it.  This is required
    because ``SubAgentEnd`` only carries ``agent_id``, not the tool-call
    id needed for the ACP ``tool_call_update``.
    """

    def __init__(self) -> None:
        # agent_id → spawned_by_tool_id (populated by SubAgentStart,
        # consumed + removed by SubAgentEnd).
        self._agent_tool_ids: dict[str, str] = {}

    async def map(
        self,
        event: Any,
        sender: ClientSender,
        session_id: str,
    ) -> None:
        """Translate one Orchestrator event into an ACP ``session/update``.

        Events that have no client-facing representation (housekeeping
        events like ``CompactionEvent``, ``QueryError``, etc.) are
        silently skipped.
        """
        notif = self._build_notification(event, session_id)
        if notif is not None:
            await sender.notify("session/update", notif)

    # ------------------------------------------------------------------
    # Private — build the SessionUpdateNotification for each event type
    # ------------------------------------------------------------------

    def _build_notification(
        self,
        event: Any,
        session_id: str,
    ) -> SessionUpdateNotification | None:
        """Return the ACP notification for *event*, or ``None`` to skip."""

        # -- Text / thought streaming --------------------------------

        if isinstance(event, TextDelta):
            return SessionUpdateNotification(
                session_id=session_id,
                update=AgentMessageChunk(
                    content=AcpTextBlock(text=event.content),
                ),
            )

        if isinstance(event, ThoughtDelta):
            return SessionUpdateNotification(
                session_id=session_id,
                update=AgentThoughtChunk(
                    content=AcpTextBlock(text=event.content),
                ),
            )

        # -- Tool call lifecycle -------------------------------------

        if isinstance(event, ToolCallStart):
            return SessionUpdateNotification(
                session_id=session_id,
                update=AcpToolCallStart(
                    tool_call_id=event.id,
                    title=event.title,
                    kind=cast(AcpToolKind, event.kind.value),
                    raw_input=event.raw_input,
                ),
            )

        if isinstance(event, ToolCallProgress):
            return SessionUpdateNotification(
                session_id=session_id,
                update=ToolCallUpdateNotification(
                    tool_call_id=event.id,
                    status="in_progress",
                    content=_content_blocks_to_dicts(event.content),
                ),
            )

        if isinstance(event, ToolCallResult):
            return SessionUpdateNotification(
                session_id=session_id,
                update=ToolCallUpdateNotification(
                    tool_call_id=event.id,
                    status="completed",
                    content=_content_blocks_to_dicts(event.content),
                ),
            )

        if isinstance(event, ToolCallError):
            return SessionUpdateNotification(
                session_id=session_id,
                update=ToolCallUpdateNotification(
                    tool_call_id=event.id,
                    status="failed",
                    content=[{"type": "text", "text": event.error}],
                ),
            )

        if isinstance(event, ToolCallDiff):
            return SessionUpdateNotification(
                session_id=session_id,
                update=ToolCallUpdateNotification(
                    tool_call_id=event.id,
                    status="completed",
                    content=[
                        {
                            "type": "diff",
                            "path": event.path,
                            "oldText": event.old_text,
                            "newText": event.new_text,
                        },
                    ],
                ),
            )

        if isinstance(event, ToolCallLocations):
            return SessionUpdateNotification(
                session_id=session_id,
                update=ToolCallUpdateNotification(
                    tool_call_id=event.id,
                    status="completed",
                    locations=[
                        ToolCallLocation(
                            path=loc["path"],
                            line=loc.get("line"),
                        )
                        for loc in event.locations
                    ],
                ),
            )

        # -- Session / UI state --------------------------------------

        if isinstance(event, PlanUpdate):
            return SessionUpdateNotification(
                session_id=session_id,
                update=AcpPlanUpdate(
                    entries=[
                        PlanEntry(
                            content=e.get("title", e.get("content", "")),
                            priority=e.get("priority", "medium"),
                            status=e.get("status", "pending"),
                        )
                        for e in event.entries
                    ],
                ),
            )

        if isinstance(event, ModeChanged):
            return SessionUpdateNotification(
                session_id=session_id,
                update=CurrentModeUpdate(mode_id=event.mode_id),
            )

        if isinstance(event, ConfigOptionChanged):
            return SessionUpdateNotification(
                session_id=session_id,
                update=ConfigOptionUpdate(config_options=_config_list(event.options)),
            )

        if isinstance(event, SessionInfoChanged):
            return SessionUpdateNotification(
                session_id=session_id,
                update=SessionInfoUpdate(title=event.title),
            )

        if isinstance(event, AvailableCommandsChanged):
            return SessionUpdateNotification(
                session_id=session_id,
                update=AvailableCommandsUpdate(
                    available_commands=event.commands,
                ),
            )

        # -- Sub-agent bracketing ------------------------------------
        # ACP has no native variant for sub-agent events.  We emit
        # them as ``tool_call_update`` on the AgentTool's call id,
        # with the agent metadata in namespaced ACP ``_meta``.

        if isinstance(event, SubAgentStart):
            self._agent_tool_ids[event.agent_id] = event.spawned_by_tool_id
            return SessionUpdateNotification(
                session_id=session_id,
                update=ToolCallUpdateNotification(
                    tool_call_id=event.spawned_by_tool_id,
                    status="in_progress",
                ),
                meta={
                    "mustang.agent/agentStart": {
                        "agent_id": event.agent_id,
                        "description": event.description,
                        "agent_type": event.agent_type,
                    },
                },
            )

        if isinstance(event, SubAgentEnd):
            tool_id = self._agent_tool_ids.pop(event.agent_id, event.agent_id)
            return SessionUpdateNotification(
                session_id=session_id,
                update=ToolCallUpdateNotification(
                    tool_call_id=tool_id,
                    status="in_progress",
                ),
                meta={
                    "mustang.agent/agentEnd": {
                        "agent_id": event.agent_id,
                        "stop_reason": event.stop_reason.value,
                    },
                },
            )

        # -- Housekeeping (not sent to client) -----------------------

        if isinstance(
            event,
            (CompactionEvent, QueryError, UserPromptBlocked, CancelledEvent),
        ):
            logger.debug(
                "AcpEventMapper: session=%s event=%s (not mapped to client)",
                session_id,
                type(event).__name__,
            )
            return None

        # -- Unknown event -------------------------------------------
        logger.warning(
            "AcpEventMapper: session=%s unknown event type %s — skipped",
            session_id,
            type(event).__name__,
        )
        return None
