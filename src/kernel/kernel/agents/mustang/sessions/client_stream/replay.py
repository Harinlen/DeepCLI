"""Replay a session's persisted events as ``session/update`` notifications.

Used during ``session/load`` to bring a fresh client up to speed: every
event that produced a user-visible update is translated back into the
ACP notification it originally triggered, in order.  Bookkeeping events
(turn lifecycle, sub-agent spans, permission roundtrips) are skipped —
the client only needs the transcript.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from kernel.core.protocol.acp.schemas.content import AcpTextBlock
from kernel.core.protocol.acp.schemas.enums import AcpToolCallStatus, AcpToolKind
from kernel.core.protocol.acp.schemas.updates import (
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
    UsageUpdate,
    UserMessageChunk,
)
from kernel.core.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.agents.mustang.sessions._shared.base import _SessionMixinBase
from kernel.agents.mustang.sessions.events import (
    AgentMessageEvent,
    AgentThoughtEvent,
    AvailableCommandsChangedEvent,
    ConfigOptionChangedEvent,
    ConversationMessageEvent,
    ModeChangedEvent,
    PlanEvent,
    SessionEvent,
    SessionInfoChangedEvent,
    ToolCallEvent,
    ToolCallUpdateEvent,
    TurnCompletedEvent,
    UserMessageEvent,
)
from kernel.agents.mustang.sessions.runtime.helpers import config_list as _config_list
from kernel.agents.mustang.sessions.runtime.state import Session

logger = logging.getLogger("kernel.agents.mustang.sessions")


class SessionReplayMixin(_SessionMixinBase):
    """Re-emits a session's persisted events to a freshly attached client."""

    async def _usage_update_for_turn(
        self,
        session: Session,
        *,
        input_tokens: int,
        output_tokens: int,
        duration_ms: int | None,
    ) -> UsageUpdate:
        """Fallback context snapshot builder for replay-only tests."""
        return UsageUpdate(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            used=max(0, input_tokens + output_tokens),
            duration_ms=duration_ms,
        )

    def _explicit_replay_keys(
        self, events: list[SessionEvent]
    ) -> tuple[set[str], set[str], set[str], set[str]]:
        """Collect explicit UI replay content so conversation fallback can de-dupe.

        Some historical logs contain partial UI rows plus complete
        ``ConversationMessageEvent`` rows.  A single global "has UI event" flag
        is too coarse: it hides assistant text whenever an unrelated or empty UI
        event exists.  Exact content keys let us prefer explicit UI chunks when
        they exist while still recovering missing transcript pieces.
        """
        user_texts: set[str] = set()
        agent_texts: set[str] = set()
        thought_texts: set[str] = set()
        tool_ids: set[str] = set()
        for event in events:
            if isinstance(event, UserMessageEvent):
                user_texts.update(_text_blocks(event.content))
            elif isinstance(event, AgentMessageEvent):
                agent_texts.update(_text_blocks(event.content))
            elif isinstance(event, AgentThoughtEvent):
                thought_texts.update(_text_blocks(event.content))
            elif isinstance(event, (ToolCallEvent, ToolCallUpdateEvent)):
                tool_ids.add(event.tool_call_id)
        return user_texts, agent_texts, thought_texts, tool_ids

    async def _replay_events(
        self, ctx: HandlerContext, session: Session, events: list[SessionEvent]
    ) -> None:
        user_texts, agent_texts, thought_texts, tool_ids = self._explicit_replay_keys(events)
        skip_conversation_users = bool(user_texts)
        skip_conversation_tools = bool(tool_ids)
        for event in events:
            await self._replay_event(
                ctx,
                session,
                event,
                skip_conversation_users=skip_conversation_users,
                skip_conversation_tools=skip_conversation_tools,
                skip_conversation_user_texts=user_texts,
                skip_conversation_agent_texts=agent_texts,
                skip_conversation_thought_texts=thought_texts,
                skip_conversation_tool_ids=tool_ids,
            )

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
        self,
        ctx: HandlerContext,
        session: Session,
        event: SessionEvent,
        *,
        skip_conversation_users: bool = False,
        skip_conversation_tools: bool = False,
        skip_conversation_user_texts: set[str] | None = None,
        skip_conversation_agent_texts: set[str] | None = None,
        skip_conversation_thought_texts: set[str] | None = None,
        skip_conversation_tool_ids: set[str] | None = None,
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

        elif isinstance(event, ConversationMessageEvent):
            await self._replay_conversation_message(
                _notify,
                event.message,
                skip_users=skip_conversation_users,
                skip_tools=skip_conversation_tools,
                skip_user_texts=skip_conversation_user_texts or set(),
                skip_agent_texts=skip_conversation_agent_texts or set(),
                skip_thought_texts=skip_conversation_thought_texts or set(),
                skip_tool_ids=skip_conversation_tool_ids or set(),
            )

        elif isinstance(event, TurnCompletedEvent):
            await _notify(
                await self._usage_update_for_turn(
                    session,
                    input_tokens=event.input_tokens,
                    output_tokens=event.output_tokens,
                    duration_ms=event.duration_ms,
                )
            )

        # session_created, session_loaded, turn_*, permission_*, sub_agent_*
        # are not replayed: the client only needs the user-visible transcript.

    async def _replay_conversation_message(
        self,
        notify: Callable[[Any], Awaitable[None]],
        message: dict[str, Any],
        *,
        skip_users: bool,
        skip_tools: bool,
        skip_user_texts: set[str],
        skip_agent_texts: set[str],
        skip_thought_texts: set[str],
        skip_tool_ids: set[str],
    ) -> None:
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, list):
            return
        if role == "user":
            if skip_users:
                return
            for block in content:
                text = block.get("text")
                if (
                    block.get("type") == "text"
                    and isinstance(text, str)
                    and text not in skip_user_texts
                ):
                    await notify(UserMessageChunk(content=AcpTextBlock(type="text", text=text)))
        elif role == "assistant":
            for block in content:
                kind = block.get("type")
                if kind == "text":
                    text = block.get("text")
                    if isinstance(text, str) and text not in skip_agent_texts:
                        await notify(
                            AgentMessageChunk(content=AcpTextBlock(type="text", text=text))
                        )
                elif kind == "thinking":
                    thinking = block.get("thinking", "")
                    if isinstance(thinking, str) and thinking not in skip_thought_texts:
                        await notify(
                            AgentThoughtChunk(content=AcpTextBlock(type="text", text=thinking))
                        )
                elif (
                    kind == "tool_use"
                    and not skip_tools
                    and str(block.get("id", "")) not in skip_tool_ids
                ):
                    await notify(
                        AcpToolCallStart(
                            tool_call_id=str(block.get("id", "")),
                            title=str(block.get("name", "tool")),
                            kind="other",
                            raw_input=json.dumps(block.get("input", {}), ensure_ascii=False),
                        )
                    )


def _text_blocks(content: list[dict[str, Any]]) -> set[str]:
    return {
        block["text"]
        for block in content
        if block.get("type") == "text" and isinstance(block.get("text"), str)
    }
