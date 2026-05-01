"""Internal housekeeping events used by Session persistence and errors.

These events are not model output.  They exist so the Session layer can record
state transitions that happen around the LLM call: compaction, cancellations,
hook blocks, and persisted history snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass

from kernel.llm.types import Message


@dataclass(frozen=True)
class CompactionEvent:
    """Emitted after conversation history is compacted.

    Token counts are estimates unless the provider has just returned usage.
    The Session layer treats them as telemetry, not as billing truth.
    """

    tokens_before: int
    tokens_after: int


@dataclass(frozen=True)
class QueryError:
    """Provider-level error surfaced to the Session layer.

    ``code`` is provider-specific when available; callers must not branch on it
    for kernel behavior because providers disagree on error taxonomies.
    """

    message: str
    code: str | None = None


@dataclass(frozen=True)
class UserPromptBlocked:
    """Emitted when ``user_prompt_submit`` blocks a query.

    Hook blocks intentionally short-circuit before the user message is appended,
    keeping rejected prompts out of persisted conversation history.
    """

    reason: str = ""


@dataclass(frozen=True)
class CancelledEvent:
    """Final event in a cancelled query stream.

    Cancellation is represented as an event rather than an exception so clients
    can close spinners and persist a terminal turn state.
    """


@dataclass(frozen=True)
class HistoryAppend:
    """Emitted after a message is appended to ConversationHistory.

    Session persistence subscribes to this event instead of peeking into
    Orchestrator internals, preserving the package boundary.
    """

    message: Message


@dataclass(frozen=True)
class HistorySnapshot:
    """Emitted after compaction replaces conversation history.

    A snapshot is used instead of multiple delete/append events because the
    compactor rewrites a prefix of history atomically.
    """

    messages: list[Message]
