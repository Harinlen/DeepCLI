"""Backward-compatible permission helpers (MVP-era API).

This module historically exposed :func:`needs_permission` as the
single permission-check entry point.  Phase 4.6 replaced that
helper with the full-featured :class:`PermissionEngine`.  The old
function is kept as a thin wrapper so existing unit tests and any
external callers continue to work.

New code should use :class:`~daemon.permissions.engine.PermissionEngine`
directly — it exposes rule-based allow/deny, denial tracking, and
mode-aware decisions that ``needs_permission`` cannot express.
"""

from __future__ import annotations

from daemon.extensions.tools.base import PermissionLevel, Tool
from daemon.permissions.modes import PermissionMode


def needs_permission(
    tool: Tool,
    mode: PermissionMode = PermissionMode.PROMPT,
) -> bool:
    """Check whether *tool* requires user confirmation in *mode*.

    Simplified MVP logic retained for compatibility:

    * ``BYPASS`` — never needs permission.
    * ``PLAN`` — everything that is not read-only needs permission.
    * ``ACCEPT_EDITS`` — file writes auto-approved, others prompt.
    * ``PROMPT`` (default) — anything above ``NONE`` prompts.

    Args:
        tool: The tool about to be executed.
        mode: Current global permission mode.

    Returns:
        ``True`` when the caller should prompt the user.
    """
    if mode == PermissionMode.BYPASS:
        return False

    if mode == PermissionMode.ACCEPT_EDITS:
        if tool.name.lower() in ("file_write", "file_edit"):
            return False
        return tool.permission_level != PermissionLevel.NONE

    if mode == PermissionMode.PLAN:
        # In plan mode only read-only level tools are silent.
        return tool.permission_level != PermissionLevel.NONE

    return tool.permission_level != PermissionLevel.NONE
