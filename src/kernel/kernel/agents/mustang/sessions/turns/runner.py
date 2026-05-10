"""Drive a single prompt turn end-to-end.

Each turn streams orchestrator events into the session log, persists the
final assistant message and turn-summary, and resolves any text-collector
future for gateway callers waiting on the full reply.  Turns are
serialised per session via the FIFO queue in ``_enqueue_turn`` /
``_run_queued_turn``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal

from kernel.agents.mustang.orchestrator import CancelledEvent, QueryError
from kernel.agents.mustang.orchestrator.types import (
    PermissionCallback,
    StopReason as OrchestratorStopReason,
)
from kernel.core.protocol.interfaces.contracts.prompt_params import PromptParams
from kernel.core.protocol.interfaces.contracts.prompt_result import PromptResult
from kernel.agents.mustang.sessions._shared.base import _SessionMixinBase
from kernel.agents.mustang.sessions.events import (
    AgentMessageEvent,
    AgentThoughtEvent,
    TurnCancelledEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
    UserMessageEvent,
)
from kernel.agents.mustang.sessions.models import TokenUsageUpdate
from kernel.agents.mustang.sessions.runtime.helpers import (
    map_orch_stop_reason as _map_orch_stop_reason,
)
from kernel.agents.mustang.sessions.runtime.state import QueuedTurn, Session, TurnState

UTC = timezone.utc
logger = logging.getLogger("kernel.agents.mustang.sessions")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder\b[^>]*>.*?</system-reminder>", re.DOTALL)
_SKILL_BLOCK_RE = re.compile(r'<skill\s+name="(?P<name>[^"]+)"[^>]*>.*?</skill>', re.DOTALL)


class SessionTurnRunnerMixin(_SessionMixinBase):
    """Executes prompt turns serially and drains the session's FIFO queue."""

    def _enqueue_turn(
        self,
        session: Session,
        params: PromptParams,
        *,
        request_id: str | int | None,
        client_turn_id: str | None = None,
        text_collector: asyncio.Future[str] | None = None,
        on_permission: PermissionCallback | None = None,
    ) -> asyncio.Future[PromptResult]:
        """Append a turn to the session FIFO and dispatch it if the loop is idle.

        Args:
            session: Target session.
            params: ACP prompt body — prompt blocks and options.
            request_id: ACP request id, recorded with the turn.
            text_collector: Gateway-only future that resolves with the
                accumulated assistant text once the turn finishes.
            on_permission: Override for the default WS-based permission
                callback.  Used by gateway adapters that prompt the user
                through their own channel.

        Returns:
            A future that resolves with the eventual ``PromptResult``.
        """
        future: asyncio.Future[PromptResult] = asyncio.get_running_loop().create_future()
        session.queue.append(
            QueuedTurn(
                request_id=request_id,
                client_turn_id=client_turn_id,
                params=params,
                queued_at=datetime.now(UTC),
                response_future=future,
                text_collector=text_collector,
                on_permission=on_permission,
            )
        )
        if session.in_flight_turn is None:
            self._maybe_dispatch_next(session)
        return future

    async def _write_first_user_message(
        self,
        session: Session,
        params: PromptParams,
        request_id: str | int | None,
        client_turn_id: str | None,
    ) -> str:
        """Persist the user prompt as the turn's opening event.

        Also seeds ``session.title`` from the first text block when the
        session does not yet have a title — gives list views something
        better than ``None`` after the very first turn.

        Args:
            session: Target session.
            params: ACP prompt body — its ``prompt`` blocks become the
                event content.
            request_id: ACP request id, recorded on the event.

        Returns:
            ``event_id`` of the new ``UserMessageEvent`` row.
        """
        content_raw = [block.model_dump() for block in params.prompt]
        user_message_event_id = await self._write_event(
            session,
            UserMessageEvent,
            content=content_raw,
            request_id=str(request_id) if request_id is not None else None,
            client_turn_id=client_turn_id,
        )
        self._maybe_set_title_from_user_message(session, content_raw)
        return user_message_event_id

    def _maybe_set_title_from_user_message(
        self,
        session: Session,
        content_raw: list[dict[str, Any]],
    ) -> None:
        """Seed ``session.title`` from the first text block when unset.

        Args:
            session: Session whose title may be updated.
            content_raw: User-message content blocks; the first
                ``{"type": "text"}`` entry contributes up to 200 chars.
        """
        if session.title is not None:
            return

        for block in content_raw:
            if block.get("type") != "text":
                continue
            first_text = _auto_title_from_text(str(block["text"]))
            if first_text is None:
                continue
            session.title = first_text
            asyncio.create_task(
                self._store.update_title(session.session_id, first_text, title_source="auto")
            )
            return

    def _turn_token_update(self, session: Session) -> tuple[int, int, TokenUsageUpdate | None]:
        """Read the orchestrator's last-turn usage and pack a ``TokenUsageUpdate``.

        Args:
            session: Session whose orchestrator just finished a turn.

        Returns:
            ``(input_tokens, output_tokens, update)``.  ``update`` is
            ``None`` for zero-token turns (cancelled or rejected) so the
            caller can skip the row UPDATE.
        """
        input_tokens, output_tokens = session.orchestrator.last_turn_usage
        if not input_tokens and not output_tokens:
            return input_tokens, output_tokens, None
        return (
            input_tokens,
            output_tokens,
            TokenUsageUpdate(
                input_tokens_delta=input_tokens,
                output_tokens_delta=output_tokens,
            ),
        )

    def _finish_text_collector(
        self,
        text_collector: asyncio.Future[str] | None,
        accumulated_text: list[str],
    ) -> None:
        """Resolve a gateway's text-collector future with the joined text.

        Args:
            text_collector: Future the gateway is awaiting, or ``None``
                when the turn was started by an ACP client.
            accumulated_text: Per-delta text fragments to ``"".join``.
        """
        if text_collector is not None and not text_collector.done():
            text_collector.set_result("".join(accumulated_text))

    async def _run_turn_core(
        self,
        session: Session,
        params: PromptParams,
        request_id: str | int | None,
        *,
        client_turn_id: str | None = None,
        text_collector: asyncio.Future[str] | None = None,
        on_permission_override: PermissionCallback | None = None,
    ) -> PromptResult:
        """Execute one prompt turn end-to-end inside the caller's task.

        Persists the user message, drives the orchestrator stream, fans
        events out to clients, and writes the closing ``TurnCompletedEvent``
        with token usage.  Cancellation is observed both inline (a yielded
        ``CancelledEvent``) and via ``asyncio.CancelledError`` from the
        outside; both paths converge on ``stop_reason="cancelled"``.

        Args:
            session: Target session.
            params: ACP prompt body driving the turn.
            request_id: ACP request id, recorded on the lifecycle events.
            text_collector: Gateway-only future that receives the full
                accumulated assistant text once the turn finishes.
            on_permission_override: Replace the default WS-based permission
                callback.  Used by gateway adapters that prompt the user
                through their own channel.

        Returns:
            ``PromptResult`` with the final ``stop_reason``.
        """
        task = asyncio.current_task()
        assert task is not None, "_run_turn_core must be invoked from an asyncio task"
        start_time = datetime.now(UTC)
        stop_reason: Literal[
            "end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled", "error"
        ] = "end_turn"

        completion_future: asyncio.Future[PromptResult] = asyncio.get_running_loop().create_future()
        session.in_flight_turn = TurnState(
            request_id=request_id,
            client_turn_id=client_turn_id,
            task=task,
            started_at=start_time,
            user_message_event_id="",
            completion_future=completion_future,
        )
        user_message_event_id = await self._write_first_user_message(
            session,
            params,
            request_id,
            client_turn_id,
        )
        if session.in_flight_turn is not None:
            session.in_flight_turn.user_message_event_id = user_message_event_id
        await self._write_event(
            session,
            TurnStartedEvent,
            request_id=request_id,
            client_turn_id=client_turn_id,
        )

        accumulated_text: list[str] = []
        accumulated_thought: list[str] = []

        permission_cb: PermissionCallback = on_permission_override or (
            lambda req: self._on_permission(session, req)
        )

        await self._agent_resource_view.check_and_refresh_before_turn()
        await self._drain_pending_mode_changes(session)

        try:
            gen = session.orchestrator.query(
                params.prompt,
                on_permission=permission_cb,
                max_turns=params.max_turns,
            )
            async for event in gen:
                if isinstance(event, CancelledEvent):
                    # The orchestrator already swallowed the CancelledError;
                    # balance the outstanding cancel so the task can resume.
                    stop_reason = "cancelled"
                    task.uncancel()
                elif isinstance(event, QueryError):
                    stop_reason = "error"
                    await self._handle_orchestrator_event(
                        session, event, accumulated_text, accumulated_thought
                    )
                else:
                    await self._handle_orchestrator_event(
                        session, event, accumulated_text, accumulated_thought
                    )

            orch_stop_reason = getattr(gen, "ag_return", None)
            if orch_stop_reason is not None and isinstance(
                orch_stop_reason, OrchestratorStopReason
            ):
                stop_reason = _map_orch_stop_reason(orch_stop_reason)

        except asyncio.CancelledError:
            stop_reason = "cancelled"
            task.uncancel()
            await self._write_event(
                session,
                TurnCancelledEvent,
                request_id=request_id,
                client_turn_id=client_turn_id,
            )

        finally:
            # Persist streamed text/thought as one event each rather than one
            # event per delta — the orchestrator stream is already broadcast
            # chunk-by-chunk; the log only needs the consolidated message.
            if accumulated_text:
                await self._write_event(
                    session,
                    AgentMessageEvent,
                    content=[{"type": "text", "text": "".join(accumulated_text)}],
                )
            if accumulated_thought:
                await self._write_event(
                    session,
                    AgentThoughtEvent,
                    content=[{"type": "text", "text": "".join(accumulated_thought)}],
                )

            duration_ms = int((datetime.now(UTC) - start_time).total_seconds() * 1000)
            input_tokens, output_tokens, token_update = self._turn_token_update(session)
            await self._write_event(
                session,
                TurnCompletedEvent,
                tokens=token_update,
                request_id=request_id,
                client_turn_id=client_turn_id,
                stop_reason=stop_reason,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            await self._broadcast(
                session,
                await self._usage_update_for_turn(
                    session,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    duration_ms=duration_ms,
                ),
            )

            self._finish_text_collector(text_collector, accumulated_text)
            result = PromptResult(
                stop_reason=stop_reason,
                meta=_turn_result_meta(client_turn_id, replayed=False),
            )
            if not completion_future.done():
                completion_future.set_result(result)

            # SubAgentEnd may have been skipped if a child query raised before
            # completion; reset depth so the next turn starts clean.
            session.subagent_depth = 0

            session.in_flight_turn = None
            dispatched = self._maybe_dispatch_next(session)
            if not dispatched:
                asyncio.create_task(self._maybe_evict(session))

        return PromptResult(
            stop_reason=stop_reason,
            meta=_turn_result_meta(client_turn_id, replayed=False),
        )

    def _maybe_dispatch_next(self, session: Session) -> bool:
        """Pop and schedule the next queued turn.

        Args:
            session: Session whose FIFO is checked.

        Returns:
            ``True`` when a turn was dispatched, ``False`` when the queue
            was empty.
        """
        if not session.queue:
            return False
        queued = session.queue.popleft()
        asyncio.create_task(
            self._run_queued_turn(session, queued),
            name=f"queued-turn-{session.session_id[:8]}",
        )
        return True

    async def _run_queued_turn(self, session: Session, queued: QueuedTurn) -> None:
        """Run one ``QueuedTurn`` and resolve its response future.

        Cancellation flips the future to ``stop_reason="cancelled"``;
        unexpected exceptions surface to the caller through the future
        rather than crashing the dispatch task.

        Args:
            session: Owning session.
            queued: Turn pulled from ``session.queue``.
        """
        try:
            result = await self._run_turn_core(
                session,
                queued.params,
                queued.request_id,
                client_turn_id=queued.client_turn_id,
                text_collector=queued.text_collector,
                on_permission_override=queued.on_permission,
            )
        except asyncio.CancelledError:
            result = PromptResult(stop_reason="cancelled")
        except Exception as exc:
            logger.exception(
                "Queued turn raised unexpectedly for session=%s",
                session.session_id,
            )
            if not queued.response_future.done():
                queued.response_future.set_exception(exc)
            return

        if not queued.response_future.done():
            queued.response_future.set_result(result)


def _turn_result_meta(client_turn_id: str | None, *, replayed: bool) -> dict[str, object] | None:
    if client_turn_id is None:
        return None
    return {
        "mustang.agent/clientTurnId": client_turn_id,
        "mustang.agent/replayedTurnResult": replayed,
    }


def _auto_title_from_text(text: str) -> str | None:
    """Return a safe one-line auto title from user-visible prompt text."""

    skill_title = _auto_title_from_skill_prompt(text)
    if skill_title is not None:
        return skill_title

    visible_text = _SYSTEM_REMINDER_RE.sub(" ", text)
    visible_text = _SKILL_BLOCK_RE.sub(" ", visible_text)
    return _collapse_title_text(visible_text)


def _auto_title_from_skill_prompt(text: str) -> str | None:
    """Summarise Kernel skill activation wrapper prompts as ``/skill args``."""

    match = _SKILL_BLOCK_RE.search(text)
    if match is None:
        return None

    name = match.group("name").strip()
    if not name:
        return None

    args_match = re.search(
        rf"User arguments for /{re.escape(name)}:\s*(?P<args>.*)\Z",
        text,
        re.DOTALL,
    )
    args = _collapse_title_text(args_match.group("args")) if args_match else None
    if args and args != "(none)":
        return _collapse_title_text(f"/{name} {args}")
    return _collapse_title_text(f"/{name}")


def _collapse_title_text(text: str) -> str | None:
    """Collapse prompt text to a display-safe title, or ``None`` if empty."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _CONTROL_CHARS_RE.sub(" ", normalized)
    collapsed = " ".join(normalized.split())
    if not collapsed:
        return None
    return collapsed[:200]
