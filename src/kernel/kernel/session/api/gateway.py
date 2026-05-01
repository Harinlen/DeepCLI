"""Session entry points for non-ACP gateways (Discord, Slack, …).

Gateway adapters do not hold a WebSocket; they create sessions directly
and await each turn's full text instead of streaming chunks.  Cross-session
messaging is exposed here so a tool running in one session can drop a
``<system-reminder>`` into another session's next turn.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from kernel.orchestrator.types import PermissionCallback
from kernel.protocol.interfaces.contracts.prompt_params import PromptParams
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.session._shared.base import _SessionMixinBase

logger = logging.getLogger("kernel.session")


class SessionGatewayMixin(_SessionMixinBase):
    """Session entry points for adapters that do not hold a WebSocket."""

    async def create_for_gateway(
        self,
        instance_id: str,
        peer_id: str,
    ) -> str:
        """Create a session without a WebSocket connection.

        Equivalent to ``new()`` minus the ``ctx.conn`` binding step.
        Used by ``GatewayAdapter`` subclasses which have no live WS
        connection.

        Args:
            instance_id: Metadata label, e.g. ``"discord:main-discord"``.
            peer_id: Platform user identifier; stored as metadata only.

        Returns:
            The new ``session_id``.
        """
        session_id = str(uuid.uuid4())
        cwd = Path.home()

        await self._create_session(
            session_id=session_id,
            cwd=cwd,
            git_branch=None,
            mcp_servers=[],
        )

        logger.info(
            "create_for_gateway: session=%s instance=%s peer=%s",
            session_id,
            instance_id,
            peer_id,
        )
        return session_id

    async def run_turn_for_gateway(
        self,
        session_id: str,
        text: str,
        on_permission: PermissionCallback,
    ) -> str:
        """Enqueue a text prompt and return the assistant's full text reply.

        Routes through the normal FIFO consumer loop so turn serialisation,
        JSONL persistence, and broadcasting to any concurrent WS observers
        all happen as usual.

        Args:
            session_id: Target session (must already exist in memory or on
                disk).
            text: Plain-text user message.
            on_permission: Called when a tool requires user approval; the
                GatewayAdapter closure typically sends a message to the
                platform user and awaits their yes/no reply.

        Returns:
            The accumulated assistant text for this turn.

        Raises:
            ResourceNotFoundError: ``session_id`` is not in memory and not
                in the DB.
        """
        # Idle gateway sessions are evicted by ``_maybe_evict`` and only
        # reloaded on demand here, so use ``_get_or_load`` rather than the
        # in-memory-only ``_get_or_raise``.
        session = await self._get_or_load(session_id)

        text_collector: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        params = PromptParams(
            session_id=session_id,
            prompt=[TextBlock(type="text", text=text)],
        )
        response_future = self._enqueue_turn(
            session,
            params,
            request_id=None,
            text_collector=text_collector,
            on_permission=on_permission,
        )

        await response_future
        return await text_collector

    def deliver_message(
        self,
        target_session_id: str,
        message: str,
        *,
        sender_session_id: str | None = None,
    ) -> bool:
        """Deliver a cross-session message to ``target_session_id``.

        The message is pushed onto the target session's
        ``pending_reminders`` buffer and rides into its next LLM turn as
        a ``<system-reminder>``.  Disk reload is intentionally not
        attempted — cross-session messaging is for active sessions only.

        Args:
            target_session_id: Recipient session id.
            message: User-supplied body to forward.
            sender_session_id: Originating session id, embedded in the
                reminder header for traceability.  ``None`` hides the
                sender label.

        Returns:
            ``True`` when the message was buffered, ``False`` when the
            target session is not currently in memory.
        """
        session = self._sessions.get(target_session_id)
        if session is None:
            return False
        sender_label = f" (from session {sender_session_id})" if sender_session_id else ""
        formatted = f"Cross-session message{sender_label}:\n{message}"
        session.pending_reminders.append(formatted)
        return True
