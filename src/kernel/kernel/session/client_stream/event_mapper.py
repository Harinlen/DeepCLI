"""Translate orchestrator events into persisted log rows + ACP updates.

For each ``OrchestratorEvent`` the dispatcher writes the matching
``SessionEvent`` and broadcasts the matching ``SessionUpdateNotification``
so the persisted log and the live stream stay in lockstep.  Mapping
helpers per event family keep the central dispatcher readable.
"""

from __future__ import annotations

import asyncio
import builtins
import logging
from datetime import datetime, timezone
from typing import Any, cast

from kernel.orchestrator import (
    AvailableCommandsChanged,
    CompactionEvent,
    ConfigOptionChanged,
    ModeChanged,
    PlanUpdate,
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
)
from kernel.orchestrator.events import HistoryAppend, HistorySnapshot
from kernel.protocol.acp.schemas.content import AcpTextBlock
from kernel.protocol.acp.schemas.enums import AcpToolCallStatus, AcpToolKind
from kernel.protocol.acp.schemas.updates import (
    AgentMessageChunk,
    AgentThoughtChunk,
    AvailableCommandsUpdate,
    ConfigOptionUpdate,
    CurrentModeUpdate,
    PlanEntry,
    PlanUpdate as AcpPlanUpdate,
    SessionInfoUpdate,
    ToolCallLocation,
    ToolCallStart as AcpToolCallStart,
    ToolCallUpdateNotification,
)
from kernel.session._shared.base import _SessionMixinBase
from kernel.session.events import (
    AvailableCommandsChangedEvent,
    ConfigOptionChangedEvent,
    ConversationMessageEvent,
    ConversationSnapshotEvent,
    ModeChangedEvent,
    PlanEvent,
    SessionInfoChangedEvent,
    SubAgentCompletedEvent,
    SubAgentSpawnedEvent,
    ToolCallEvent,
    ToolCallUpdateEvent,
)
from kernel.session.message_serde import serialize_message
from kernel.session.runtime.helpers import config_list as _config_list
from kernel.session.runtime.state import Session

logger = logging.getLogger("kernel.session")


class SessionEventMapperMixin(_SessionMixinBase):
    """Translates orchestrator events into log rows + ACP update broadcasts."""

    async def _emit_tool_update(
        self,
        session: Session,
        *,
        tool_call_id: str,
        status: AcpToolCallStatus,
        content: list[dict[str, Any]] | None = None,
        persisted_content: list[dict[str, Any]] | None = None,
        locations: list[ToolCallLocation] | None = None,
    ) -> None:
        """Persist + broadcast one tool-call status change.

        Args:
            session: Owning session.
            tool_call_id: Id of the tool call being updated.
            status: New ACP status (``pending``/``in_progress``/
                ``completed``/``failed``).
            content: Inline preview shown to the connected client.
            persisted_content: Form written to the log — typically
                ``content`` after ``_maybe_spill`` has externalised
                oversized output.  Defaults to ``content`` when ``None``.
            locations: Optional file locations referenced by the tool call.
        """
        await self._write_event(
            session,
            ToolCallUpdateEvent,
            tool_call_id=tool_call_id,
            status=status,
            content=persisted_content if persisted_content is not None else content,
        )
        await self._broadcast(
            session,
            ToolCallUpdateNotification(
                tool_call_id=tool_call_id,
                status=status,
                content=content,
                locations=locations,
            ),
        )

    async def _handle_tool_result(self, session: Session, event: ToolCallResult) -> None:
        """Emit the ``completed`` update for a finished tool call.

        Args:
            session: Owning session.
            event: Orchestrator event carrying the tool's final blocks.
        """
        content_raw = self._blocks_to_raw(event.content)
        await self._emit_tool_update(
            session,
            tool_call_id=event.id,
            status="completed",
            content=content_raw,
            persisted_content=self._maybe_spill(session, event.id, content_raw),
        )

    async def _handle_tool_locations(self, session: Session, event: ToolCallLocations) -> None:
        """Broadcast file locations a still-running tool call has surfaced.

        Args:
            session: Owning session.
            event: Orchestrator event carrying ``{path, line}`` dicts.
        """
        locations = [
            ToolCallLocation(path=location["path"], line=location.get("line"))
            for location in event.locations
        ]
        await self._broadcast(
            session,
            ToolCallUpdateNotification(
                tool_call_id=event.id,
                status="in_progress",
                locations=locations,
            ),
        )

    async def _handle_config_options(self, session: Session, event: ConfigOptionChanged) -> None:
        """Mirror an orchestrator-driven config snapshot into the session log.

        Args:
            session: Owning session whose ``config_options`` is updated.
            event: Orchestrator event carrying the full option mapping.
        """
        full_state: dict[str, Any] = dict(event.options)
        session.config_options.update({key: str(value) for key, value in full_state.items()})
        await self._write_event(
            session,
            ConfigOptionChangedEvent,
            config_id="",
            value="",
            full_state=full_state,
        )
        await self._broadcast(session, ConfigOptionUpdate(config_options=_config_list(full_state)))

    async def _persist_history_append(self, session: Session, event: HistoryAppend) -> None:
        """Write one orchestrator history append into the session log.

        Args:
            session: Owning session.
            event: Orchestrator event carrying the new ``Message`` dataclass.
        """
        try:
            await self._write_event(
                session,
                ConversationMessageEvent,
                message=serialize_message(event.message),
            )
        except Exception:
            logger.warning(
                "session=%s: failed to serialize HistoryAppend — skipping",
                session.session_id,
                exc_info=True,
            )

    async def _persist_history_snapshot(self, session: Session, event: HistorySnapshot) -> None:
        """Write a post-compaction history snapshot to the session log.

        Args:
            session: Owning session.
            event: Orchestrator event carrying the compacted message list.
        """
        try:
            await self._write_event(
                session,
                ConversationSnapshotEvent,
                messages=[serialize_message(message) for message in event.messages],
            )
        except Exception:
            logger.warning(
                "session=%s: failed to serialize HistorySnapshot — skipping",
                session.session_id,
                exc_info=True,
            )

    async def _handle_orchestrator_event(
        self,
        session: "Session",
        event: Any,
        accumulated_text: "builtins.list[str]",
        accumulated_thought: "builtins.list[str]",
    ) -> None:
        """Dispatch one orchestrator event to its persist + broadcast pair.

        Args:
            session: Owning session.
            event: One ``OrchestratorEvent`` yielded by the active turn.
            accumulated_text: Buffer the runner flushes as a single
                ``AgentMessage`` row when the turn ends; ``TextDelta``
                events append here.
            accumulated_thought: Same shape as ``accumulated_text`` but
                for ``ThoughtDelta`` events.
        """
        if isinstance(event, TextDelta):
            accumulated_text.append(event.content)
            await self._broadcast(
                session,
                AgentMessageChunk(content=AcpTextBlock(type="text", text=event.content)),
            )

        elif isinstance(event, ThoughtDelta):
            accumulated_thought.append(event.content)
            await self._broadcast(
                session,
                AgentThoughtChunk(content=AcpTextBlock(type="text", text=event.content)),
            )

        elif isinstance(event, ToolCallStart):
            kind = _acp_tool_kind(event.kind.value)
            await self._write_event(
                session,
                ToolCallEvent,
                tool_call_id=event.id,
                title=event.title,
                kind=kind,
                raw_input=event.raw_input,
            )
            await self._broadcast(
                session,
                AcpToolCallStart(
                    tool_call_id=event.id,
                    title=event.title,
                    kind=kind,
                    raw_input=event.raw_input,
                ),
            )

        elif isinstance(event, ToolCallProgress):
            content_raw = self._blocks_to_raw(event.content)
            await self._emit_tool_update(
                session,
                tool_call_id=event.id,
                status="in_progress",
                content=content_raw,
            )

        elif isinstance(event, ToolCallResult):
            await self._handle_tool_result(session, event)

        elif isinstance(event, ToolCallError):
            await self._emit_tool_update(
                session,
                tool_call_id=event.id,
                status="failed",
                content=[{"type": "text", "text": event.error}],
            )

        elif isinstance(event, ToolCallDiff):
            await self._emit_tool_update(
                session,
                tool_call_id=event.id,
                status="completed",
                content=[
                    {
                        "type": "diff",
                        "path": event.path,
                        "old_text": event.old_text,
                        "new_text": event.new_text,
                    }
                ],
            )

        elif isinstance(event, ToolCallLocations):
            await self._handle_tool_locations(session, event)

        elif isinstance(event, PlanUpdate):
            await self._write_event(session, PlanEvent, entries=event.entries)
            await self._broadcast(
                session,
                AcpPlanUpdate(entries=[PlanEntry(**e) for e in event.entries]),
            )

        elif isinstance(event, ModeChanged):
            old_mode = session.mode_id
            session.mode_id = event.mode_id
            await self._write_event(
                session,
                ModeChangedEvent,
                mode_id=event.mode_id,
                from_mode=old_mode,
            )
            await self._broadcast(session, CurrentModeUpdate(mode_id=event.mode_id))

        elif isinstance(event, ConfigOptionChanged):
            await self._handle_config_options(session, event)

        elif isinstance(event, SessionInfoChanged):
            if event.title is not None:
                record = await self._store.get_session(session.session_id)
                if record is not None and record.title_source == "user":
                    return
                session.title = event.title
                asyncio.create_task(
                    self._store.update_title(
                        session.session_id,
                        event.title,
                        title_source="auto",
                    )
                )
            await self._write_event(session, SessionInfoChangedEvent, title=event.title)
            await self._broadcast(
                session,
                SessionInfoUpdate(
                    title=event.title,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                ),
            )

        elif isinstance(event, AvailableCommandsChanged):
            await self._write_event(
                session,
                AvailableCommandsChangedEvent,
                commands=event.commands,
            )
            await self._broadcast(
                session,
                AvailableCommandsUpdate(available_commands=event.commands),
            )

        elif isinstance(event, SubAgentStart):
            session.subagent_depth += 1
            await self._write_event(
                session,
                SubAgentSpawnedEvent,
                agent_id=event.agent_id,
                agent_type=event.agent_type,
                description=event.description,
            )
            await self._broadcast(
                session,
                AgentMessageChunk(
                    content=AcpTextBlock(type="text", text=""),
                    meta={
                        "mustang.agent/agentStart": {
                            "agent_id": event.agent_id,
                            "agent_type": event.agent_type,
                            "description": event.description,
                        }
                    },
                ),
            )

        elif isinstance(event, SubAgentEnd):
            session.subagent_depth = max(0, session.subagent_depth - 1)
            await self._write_event(
                session,
                SubAgentCompletedEvent,
                agent_id=event.agent_id,
                stop_reason=event.stop_reason.value,
            )
            await self._broadcast(
                session,
                AgentMessageChunk(
                    content=AcpTextBlock(type="text", text=""),
                    meta={
                        "mustang.agent/agentEnd": {
                            "agent_id": event.agent_id,
                            "stop_reason": event.stop_reason.value,
                        }
                    },
                ),
            )

        elif isinstance(event, HistoryAppend):
            if session.subagent_depth == 0:
                await self._persist_history_append(session, event)

        elif isinstance(event, HistorySnapshot):
            if session.subagent_depth == 0:
                await self._persist_history_snapshot(session, event)

        elif isinstance(event, CompactionEvent):
            logger.debug(
                "session=%s compaction: %d → %d tokens",
                session.session_id,
                event.tokens_before,
                event.tokens_after,
            )


def _acp_tool_kind(kind: str) -> AcpToolKind:
    """Map DeepCLI-only tool kinds onto ACP's narrower enum."""
    allowed = {"read", "edit", "execute", "search", "fetch", "think", "delete", "move", "other"}
    return cast(AcpToolKind, kind if kind in allowed else "other")
