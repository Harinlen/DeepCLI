"""Result of archiving or unarchiving a session."""

from __future__ import annotations

from kernel.protocol.interfaces.contracts.list_sessions_result import SessionSummary


class ArchiveSessionResult(SessionSummary):
    """Updated session summary after archive state change."""
