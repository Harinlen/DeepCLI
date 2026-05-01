"""SQLAlchemy ORM models and dataclasses for session SQLite storage.

Two tables:
- ``sessions``       — one row per session (ConversationRecord ORM mapped class)
- ``session_events`` — append-only event log (Core Table, no ORM mapping)

``TokenUsageUpdate`` is a plain dataclass passed alongside event writes to
accumulate per-turn token deltas into the sessions row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for every session ORM model."""


class ConversationRecord(Base):
    """ORM model for the ``sessions`` table.

    Named ``ConversationRecord`` (not ``Session``) to avoid confusion with
    SQLAlchemy's ``AsyncSession`` and the runtime ``Session`` dataclass.

    ``created`` and ``modified`` are ISO-8601 UTC strings.  ``modified``
    carries ``onupdate=_now_iso`` which fires automatically on ORM-path
    UPDATEs; Core UPDATE statements must include ``modified`` explicitly.
    """

    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(sa.String, primary_key=True)
    cwd: Mapped[str] = mapped_column(sa.String, nullable=False)
    title: Mapped[str | None] = mapped_column(sa.String)
    title_source: Mapped[str | None] = mapped_column(sa.String)
    archived_at: Mapped[str | None] = mapped_column(sa.String)
    created: Mapped[str] = mapped_column(sa.String, nullable=False, default=_now_iso)
    modified: Mapped[str] = mapped_column(
        sa.String, nullable=False, default=_now_iso, onupdate=_now_iso
    )
    total_input_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)


# Append-only event log, kept as a Core Table (no ORM mapping) because every
# query is a session_id-scoped scan; ordering is by the timestamp column.
session_events = sa.Table(
    "session_events",
    Base.metadata,
    sa.Column("session_id", sa.String, nullable=False),
    sa.Column("timestamp", sa.String, nullable=False),
    sa.Column("context", sa.Text, nullable=False),
    sa.Index("idx_events_session", "session_id"),
)


@dataclass
class TokenUsageUpdate:
    """Token counters to add to the ``sessions`` row alongside an event write.

    Both fields default to 0.  Only non-zero deltas produce UPDATE clauses.
    ``ConversationRecord.modified`` must be included explicitly in every Core
    UPDATE — the ``onupdate`` hook only fires on ORM flush.
    """

    input_tokens_delta: int = 0
    output_tokens_delta: int = 0
