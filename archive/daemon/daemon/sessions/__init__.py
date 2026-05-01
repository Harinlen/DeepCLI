"""Session management — persistence, lifecycle, and multi-connection support.

Sessions track conversation history as JSONL transcripts and manage
the mapping between WebSocket connections and Orchestrator instances.
Each session owns one Orchestrator; multiple WS clients can subscribe
to the same session for real-time event broadcasting.
"""

from daemon.sessions.cleanup import cleanup_expired_sessions, start_cleanup_task
from daemon.sessions.entry import (
    AssistantMessageEntry,
    BaseEntry,
    CompactBoundaryEntry,
    Entry,
    SessionMetaEntry,
    ToolCallEntry,
    UserMessageEntry,
)
from daemon.sessions.manager import Session, SessionManager
from daemon.sessions.storage import SessionMeta, TranscriptWriter

__all__ = [
    "AssistantMessageEntry",
    "BaseEntry",
    "CompactBoundaryEntry",
    "Entry",
    "Session",
    "SessionManager",
    "SessionMeta",
    "SessionMetaEntry",
    "ToolCallEntry",
    "TranscriptWriter",
    "UserMessageEntry",
    "cleanup_expired_sessions",
    "start_cleanup_task",
]
