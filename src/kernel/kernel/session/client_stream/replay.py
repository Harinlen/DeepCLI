"""Replay a session's persisted events as ``session/update`` notifications.

Used during ``session/load`` to bring a fresh client up to speed: every
event that produced a user-visible update is translated back into the
ACP notification it originally triggered, in order.  Bookkeeping events
(turn lifecycle, sub-agent spans, permission roundtrips) are skipped —
the client only needs the transcript.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

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
    SessionUpdateNotification,
    ToolCallLocation,
    ToolCallStart as AcpToolCallStart,
    ToolCallUpdateNotification,
    UserMessageChunk,
)
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.session._shared.base import _SessionMixinBase
from kernel.session.events import (
    AgentMessageEvent,
    AgentThoughtEvent,
    AvailableCommandsChangedEvent,
    ConfigOptionChangedEvent,
    ModeChangedEvent,
    PlanEvent,
    SessionEvent,
    SessionInfoChangedEvent,
    ToolCallEvent,
    ToolCallUpdateEvent,
    UserMessageEvent,
)
from kernel.session.runtime.helpers import config_list as _config_list
from kernel.session.runtime.state import Session

logger = logging.getLogger("kernel.session")


class SessionReplayMixin(_SessionMixinBase):
    """Re-emits a session's persisted events to a freshly attached client."""

    async def _replay_text_blocks(
        self,
        notify: Callable[[Any], Awaitable[None]],
        content: list[dict[str, Any]],
        chunk_cls: type,
    ) -> None:
        """Re-emit each text block in ``content`` as one chunk update.

        Args:
            notify: Callable that pushes one ``session/update`` notification.
            content: Stored content blocks; only ``{"type": "text"}``
                entries produce chunks, others are skipped.
            chunk_cls: Update class to instantiate per text block —
                ``AgentMessageChunk`` for agent messages,
                ``UserMessageChunk`` for user prompts, and so on.
        """
        for block_dict in content:
            if block_dict.get("type") == "text":
                await notify(chunk_cls(content=AcpTextBlock(type="text", text=block_dict["text"])))

    def _restore_tool_content(
        self, session: Session, content_blocks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Inline any ``spilled`` blocks by reading the sidecar file.

        Args:
            session: Owning session — its directory holds the sidecar files.
            content_blocks: Persisted blocks; ``{"type": "spilled", …}``
                entries are replaced with the inlined text.

        Returns:
            Blocks safe to send to the client: ``spilled`` entries become
            ``text``, every other block passes through.  If the sidecar
            cannot be read the stored ``preview`` is used so the client
            still sees a sensible truncation.
        """
        restored: list[dict[str, Any]] = []
        for block in content_blocks:
            if block.get("type") != "spilled":
                restored.append(block)
                continue
            try:
                result_hash = Path(block["path"]).stem
                restored.append(
                    {
                        "type": "text",
                        "text": self._store.read_spilled(session.session_id, result_hash),
                    }
                )
            except Exception:
                restored.append({"type": "text", "text": block.get("preview", "")})
        return restored

    async def _replay_event(
        self, ctx: HandlerContext, session: Session, event: SessionEvent
    ) -> None:
        """Send one stored event to ``ctx.sender`` as a ``session/update``.

        Args:
            ctx: Handler context for the joining connection.
            session: Owning session — used to scope spillover lookups.
            event: One persisted event from the log; events that have no
                client-visible counterpart (turn lifecycle, sub-agent
                spans, …) are skipped silently.
        """
        sid = session.session_id

        async def _notify(update: Any) -> None:
            await ctx.sender.notify(
                "session/update",
                SessionUpdateNotification(session_id=sid, update=update),
            )

        if isinstance(event, UserMessageEvent):
            for block_dict in event.content:
                try:
                    if block_dict.get("type") == "text":
                        await _notify(
                            UserMessageChunk(content=AcpTextBlock.model_validate(block_dict))
                        )
                except Exception:
                    logger.debug(
                        "session=%s: skipping malformed user text block during replay",
                        session.session_id,
                    )

        elif isinstance(event, AgentMessageEvent):
            await self._replay_text_blocks(_notify, event.content, AgentMessageChunk)

        elif isinstance(event, AgentThoughtEvent):
            await self._replay_text_blocks(_notify, event.content, AgentThoughtChunk)

        elif isinstance(event, ToolCallEvent):
            await _notify(
                AcpToolCallStart(
                    tool_call_id=event.tool_call_id,
                    title=event.title,
                    kind=cast(AcpToolKind, event.kind),
                    raw_input=event.raw_input,
                )
            )

        elif isinstance(event, ToolCallUpdateEvent):
            locations = [
                ToolCallLocation(path=loc["path"], line=loc.get("line"))
                for loc in (event.locations or [])
            ]
            await _notify(
                ToolCallUpdateNotification(
                    tool_call_id=event.tool_call_id,
                    status=cast(AcpToolCallStatus, event.status),
                    content=self._restore_tool_content(session, list(event.content or [])) or None,
                    locations=locations or None,
                )
            )

        elif isinstance(event, PlanEvent):
            await _notify(AcpPlanUpdate(entries=[PlanEntry(**e) for e in event.entries]))

        elif isinstance(event, ModeChangedEvent):
            await _notify(CurrentModeUpdate(mode_id=event.mode_id))

        elif isinstance(event, ConfigOptionChangedEvent):
            await _notify(ConfigOptionUpdate(config_options=_config_list(event.full_state)))

        elif isinstance(event, SessionInfoChangedEvent):
            await _notify(
                SessionInfoUpdate(title=event.title, updated_at=event.timestamp.isoformat())
            )

        elif isinstance(event, AvailableCommandsChangedEvent):
            await _notify(AvailableCommandsUpdate(available_commands=event.commands))

        # session_created, session_loaded, turn_*, permission_*, sub_agent_*
        # are not replayed: the client only needs the user-visible transcript.
