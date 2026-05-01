"""Conversation history management.

Tracks messages in a single conversation. Provides methods to append
messages and export the history in the universal format that providers
expect.

Mutators are ``async`` and protected by an :class:`asyncio.Lock` so
that parallel tool execution (Phase 5.3) and sub-agent injection
(Phase 5.2) cannot corrupt the message list.  A synchronous
:meth:`_append` escape hatch is provided for
:func:`~daemon.sessions.rebuild.rebuild_conversation`, which runs
in a single-threaded context before the session enters the async
world.
"""

from __future__ import annotations

import asyncio

from daemon.providers.base import (
    ImageContent,
    Message,
    MessageContent,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)


class Conversation:
    """In-memory conversation history for a single session.

    Stores universal ``Message`` objects.  The orchestrator appends
    messages as the conversation progresses; providers receive the
    full history via :meth:`get_messages`.

    All public mutators are ``async`` and serialised by an internal
    :class:`asyncio.Lock`.  Read-only accessors remain synchronous.
    """

    def __init__(self) -> None:
        self._messages: list[Message] = []
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Sync escape hatch (for rebuild / tests that run outside an event loop)
    # ------------------------------------------------------------------

    def _append(self, msg: Message) -> None:
        """Append without locking — **only** for session rebuild."""
        self._messages.append(msg)

    def _replace(self, messages: list[Message]) -> None:
        """Replace without locking — **only** for session rebuild."""
        self._messages = list(messages)

    # ------------------------------------------------------------------
    # Async mutators (locked)
    # ------------------------------------------------------------------

    async def add_user_message(self, text: str) -> Message:
        """Append a user message and return it."""
        msg = Message.user(text)
        async with self._lock:
            self._messages.append(msg)
        return msg

    async def add_assistant_text(self, text: str) -> Message:
        """Append an assistant text response and return it."""
        msg = Message.assistant_text(text)
        async with self._lock:
            self._messages.append(msg)
        return msg

    async def add_assistant_message(self, content: list[MessageContent]) -> Message:
        """Append an assistant message with arbitrary content blocks.

        Used when the assistant response contains mixed content
        (e.g. text + tool calls accumulated during streaming).
        """
        msg = Message(role="assistant", content=content)
        async with self._lock:
            self._messages.append(msg)
        return msg

    async def add_tool_result(
        self,
        tool_call_id: str,
        output: str,
        *,
        is_error: bool = False,
        image_parts: list[ImageContent] | None = None,
    ) -> Message:
        """Append a tool-result message and return it."""
        msg = Message.tool_result(tool_call_id, output, is_error, image_parts=image_parts)
        async with self._lock:
            self._messages.append(msg)
        return msg

    async def replace_messages(self, messages: list[Message]) -> None:
        """Replace all messages with a new list (used after compaction).

        Args:
            messages: The replacement message list (typically a
                compaction summary followed by recent messages).
        """
        async with self._lock:
            self._messages = list(messages)

    async def clear(self) -> None:
        """Remove all messages (``/clear`` command)."""
        async with self._lock:
            self._messages.clear()

    # ------------------------------------------------------------------
    # Accessors (read-only, no lock needed)
    # ------------------------------------------------------------------

    def get_messages(self) -> list[Message]:
        """Return a shallow copy of the message history."""
        return list(self._messages)

    @property
    def message_count(self) -> int:
        """Number of messages in the conversation."""
        return len(self._messages)

    @property
    def last_assistant_text(self) -> str | None:
        """Extract text from the most recent assistant message, or ``None``."""
        for msg in reversed(self._messages):
            if msg.role == "assistant":
                parts = [c.text for c in msg.content if isinstance(c, TextContent)]
                if parts:
                    return "\n".join(parts)
        return None

    def all_unresolved_tool_calls(self) -> list[ToolUseContent]:
        """Scan every assistant message for unresolved ``tool_use``.

        Unlike :attr:`pending_tool_calls` (which only inspects the
        tail), this walks the full history.  Used by
        :meth:`strip_orphaned_tool_calls` to sweep *interior* orphans
        that crept in from old JSONL or escaped the finalize path.

        Returns:
            Every ``tool_use`` block without a matching ``tool_result``
            anywhere later in the history.
        """
        # Collect every resolved id in one pass.
        resolved_ids: set[str] = set()
        for msg in self._messages:
            if msg.role == "tool":
                for c in msg.content:
                    if isinstance(c, ToolResultContent):
                        resolved_ids.add(c.tool_call_id)

        # Any tool_use whose id is not resolved is an orphan.
        orphans: list[ToolUseContent] = []
        for msg in self._messages:
            if msg.role != "assistant":
                continue
            for c in msg.content:
                if isinstance(c, ToolUseContent) and c.tool_call_id not in resolved_ids:
                    orphans.append(c)
        return orphans

    async def strip_orphaned_tool_calls(self) -> int:
        """Remove unresolved ``tool_use`` blocks from the conversation.

        Scans every assistant message (not just the tail) so
        **interior orphans** — e.g. from a JSONL file written before
        Mustang had cancellation-finalizers, or any escape from
        :meth:`Orchestrator._finalize_cancelled_calls` — are also
        cleaned up.

        Also drops ``tool``-role messages whose ``tool_call_id``
        references one of the stripped calls (orphaned results from
        the other direction).

        Returns:
            Number of ``tool_use`` blocks removed.  ``0`` when the
            conversation is already clean — safe to call before
            every ``add_user_message``.
        """
        async with self._lock:
            return self._strip_orphaned_unlocked()

    def _strip_orphaned_unlocked(self) -> int:
        """Lock-free implementation of :meth:`strip_orphaned_tool_calls`.

        Also called by rebuild (sync context) via the public
        ``strip_orphaned_tool_calls_sync`` method.
        """
        orphans = self.all_unresolved_tool_calls()
        if not orphans:
            return 0

        orphan_ids = {tc.tool_call_id for tc in orphans}

        # Strip orphaned tool_use from every assistant message.
        for msg in list(self._messages):
            if msg.role != "assistant":
                continue
            msg.content = [
                c
                for c in msg.content
                if not (isinstance(c, ToolUseContent) and c.tool_call_id in orphan_ids)
            ]
            if not msg.content:
                self._messages.remove(msg)

        # Drop any tool-role message whose only content referenced
        # a stripped call.
        surviving: list[Message] = []
        for msg in self._messages:
            if msg.role != "tool":
                surviving.append(msg)
                continue
            remaining = [
                c
                for c in msg.content
                if not (isinstance(c, ToolResultContent) and c.tool_call_id in orphan_ids)
            ]
            if remaining:
                msg.content = remaining
                surviving.append(msg)
        self._messages = surviving

        return len(orphans)

    def strip_orphaned_tool_calls_sync(self) -> int:
        """Synchronous variant for session rebuild (no event loop)."""
        return self._strip_orphaned_unlocked()

    @property
    def pending_tool_calls(self) -> list[ToolUseContent]:
        """Return unresolved tool calls from the last assistant message.

        A tool call is "pending" when there is no subsequent
        ``tool``-role message with the same ``tool_call_id``.
        """
        resolved_ids: set[str] = set()
        pending: list[ToolUseContent] = []

        # Walk backwards — first collect resolved IDs, then find pending
        for msg in reversed(self._messages):
            if msg.role == "tool":
                for c in msg.content:
                    if isinstance(c, ToolResultContent):
                        resolved_ids.add(c.tool_call_id)
            elif msg.role == "assistant":
                for c in msg.content:
                    if isinstance(c, ToolUseContent) and c.tool_call_id not in resolved_ids:
                        pending.append(c)
                # Only check the last assistant message
                break

        return pending
