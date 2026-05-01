"""Result of renaming a session."""

from __future__ import annotations

from kernel.protocol.interfaces.contracts.list_sessions_result import SessionSummary


class RenameSessionResult(SessionSummary):
    """Updated session summary after rename."""
