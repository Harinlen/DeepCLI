"""Rebuild a ``Session`` from its persisted event log.

``_LoadedSessionState`` walks the event sequence to derive the latest
metadata and conversation history; ``_load_from_disk`` then restores the
worktree cwd and reconstructs the orchestrator with that history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kernel.llm.types import AssistantMessage, Message
from kernel.session._shared.base import _SessionMixinBase
from kernel.session.events import (
    ConfigOptionChangedEvent,
    ConversationMessageEvent,
    ConversationSnapshotEvent,
    ModeChangedEvent,
    SessionCreatedEvent,
    SessionEvent,
    SessionInfoChangedEvent,
)
from kernel.session.message_serde import deserialize_message
from kernel.session.runtime.state import Session

UTC = timezone.utc
logger = logging.getLogger("kernel.session")


@dataclass
class _LoadedSessionState:
    cwd_str: str = field(default_factory=lambda: str(Path.cwd()))
    git_branch: str | None = None
    mode_id: str | None = None
    config_options: dict[str, str] = field(default_factory=dict)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    title: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_event_id: str | None = None
    history_messages: list[Message] = field(default_factory=list)

    def apply(self, event: SessionEvent, *, session_id: str) -> None:
        """Fold one persisted event into the running state.

        Args:
            event: Next event from the log.  Each event class moves a
                different field forward; unrecognised event types only
                bump ``last_event_id`` and ``updated_at``.
            session_id: Owning session id, used in deserialise warnings.
        """
        self.last_event_id = event.event_id
        self.updated_at = event.timestamp
        if isinstance(event, SessionCreatedEvent):
            self.cwd_str = event.cwd
            self.git_branch = event.git_branch
            self.mcp_servers = event.mcp_servers
            self.created_at = event.timestamp
        elif isinstance(event, ModeChangedEvent):
            self.mode_id = event.mode_id
        elif isinstance(event, ConfigOptionChangedEvent) and event.config_id:
            self.config_options[event.config_id] = event.value
        elif isinstance(event, SessionInfoChangedEvent) and event.title:
            self.title = event.title
        elif isinstance(event, ConversationSnapshotEvent):
            self._replace_history_from_snapshot(event, session_id=session_id)
        elif isinstance(event, ConversationMessageEvent):
            self._append_history_message(event, session_id=session_id)

    def _replace_history_from_snapshot(
        self,
        event: ConversationSnapshotEvent,
        *,
        session_id: str,
    ) -> None:
        """Replace the in-progress history with a post-compaction snapshot.

        Args:
            event: Snapshot row carrying the compacted message list.
            session_id: Owning session id, used in deserialise warnings.
        """
        try:
            self.history_messages = [deserialize_message(message) for message in event.messages]
        except Exception:
            logger.warning(
                "session=%s: failed to deserialize ConversationSnapshotEvent — "
                "history will be incomplete",
                session_id,
                exc_info=True,
            )

    def _append_history_message(
        self,
        event: ConversationMessageEvent,
        *,
        session_id: str,
    ) -> None:
        """Append one persisted message, collapsing assistant retries.

        Args:
            event: Conversation message row from the log.
            session_id: Owning session id, used in deserialise warnings.

        Deserialise failures are logged and the message dropped — a single
        bad row must not break resume for the rest of the session.
        """
        try:
            message = deserialize_message(event.message)
        except Exception:
            logger.warning(
                "session=%s: failed to deserialize ConversationMessageEvent — skipping",
                session_id,
                exc_info=True,
            )
            return

        # Two assistant messages in a row mean the earlier one was popped
        # for retry, so keep only the most recent persisted response.
        if (
            self.history_messages
            and isinstance(self.history_messages[-1], AssistantMessage)
            and isinstance(message, AssistantMessage)
        ):
            self.history_messages[-1] = message
            return
        self.history_messages.append(message)


class SessionLoaderMixin(_SessionMixinBase):
    """Rebuilds an in-memory ``Session`` from its persisted event log."""

    def _reconstruct_state(
        self, session_id: str, events: list[SessionEvent]
    ) -> _LoadedSessionState:
        """Replay the event log to derive the session's latest persisted state.

        Args:
            session_id: Owning session id (passed through to ``apply``).
            events: Full event log, in append order.

        Returns:
            ``_LoadedSessionState`` carrying the folded metadata and
            history.
        """
        state = _LoadedSessionState()
        for event in events:
            state.apply(event, session_id=session_id)
        return state

    async def _restore_worktree_cwd(self, session_id: str, cwd: Path) -> Path:
        """Return the session's worktree path if Git tracks one, else ``cwd``.

        Sessions that opted into a worktree at ``session/new`` time are
        re-pointed at the same worktree on resume so file paths in the
        replayed history still resolve.

        Args:
            session_id: Session being resumed.
            cwd: Persisted ``cwd`` from the original ``SessionCreatedEvent``.

        Returns:
            The worktree path when Git can restore one, otherwise ``cwd``
            unchanged.  Restore failures are logged and degrade to ``cwd``.
        """
        try:
            from kernel.git import GitManager

            git_manager = self._module_table.get(GitManager)
            worktree_session = await git_manager.restore_worktree_for_session(session_id)
            if worktree_session is None:
                return cwd
            logger.info(
                "Restored worktree cwd for session %s: %s",
                session_id,
                worktree_session.worktree_path,
            )
            return worktree_session.worktree_path
        except (KeyError, ImportError):
            return cwd
        except Exception:
            logger.exception("Failed to restore worktree for session %s", session_id)
            return cwd

    async def _load_from_disk(self, session_id: str) -> None:
        """Reconstruct an in-memory ``Session`` from the persisted event log.

        Args:
            session_id: Session to load.  The caller (typically
                ``_get_or_load`` or ``load_session``) must have already
                confirmed it exists in the DB.
        """
        events = await self._store.read_events(session_id)
        state = self._reconstruct_state(session_id, events)
        cwd = await self._restore_worktree_cwd(session_id, Path(state.cwd_str))

        if state.history_messages:
            logger.info(
                "session=%s: reconstructed %d history messages for resume",
                session_id,
                len(state.history_messages),
            )
        orchestrator, task_registry = self._make_orchestrator(
            session_id, cwd, state.history_messages, None
        )

        session = Session(
            session_id=session_id,
            cwd=cwd,
            created_at=state.created_at,
            updated_at=state.updated_at,
            title=state.title,
            git_branch=state.git_branch,
            mode_id=state.mode_id,
            config_options=state.config_options,
            mcp_servers=state.mcp_servers,
            orchestrator=orchestrator,
            task_registry=task_registry,
            last_event_id=state.last_event_id,
        )
        self._sessions[session_id] = session
