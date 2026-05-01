"""Permission modes and read-only tool classification.

The :class:`PermissionMode` enum controls how the
:class:`~daemon.permissions.engine.PermissionEngine` handles tool
calls at a session-wide level:

* ``PROMPT`` (value ``"default"``) — ask the user for any tool
  above ``NONE``.  Matches Claude Code's ``default`` mode.
* ``ACCEPT_EDITS`` — auto-approve file writes; still ask for bash.
* ``PLAN`` — only read-only tools plus the plan-file are allowed.
* ``BYPASS`` — approve everything without asking.

Backward compatibility: the value string ``"prompt"`` (Mustang's
original MVP name) is mapped to ``"default"`` at config load time in
:func:`daemon.config.defaults._coerce_permission_mode`.
"""

from __future__ import annotations

import enum


class PermissionMode(enum.Enum):
    """Global permission mode for a session.

    The enum **name** is kept stable (``PROMPT``) so existing Python
    callers do not break.  The enum **value** is the user-facing
    string that appears in ``config.yaml`` / ``settings.json``.
    """

    PROMPT = "default"
    ACCEPT_EDITS = "accept_edits"
    PLAN = "plan"
    BYPASS = "bypass"


# Tools whose worst-case effect is reading data.  ``PLAN`` mode
# allows these without confirmation; any other tool is denied in
# plan mode unless it targets the active plan file.
_READ_ONLY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "file_read",
        "glob",
        "grep",
    }
)


def is_read_only_tool(tool_name: str) -> bool:
    """Return ``True`` if *tool_name* is a read-only builtin.

    Used by :class:`~daemon.permissions.engine.PermissionEngine` to
    decide what is allowed under :attr:`PermissionMode.PLAN`.  The
    lookup is case-insensitive.
    """
    return tool_name.lower() in _READ_ONLY_TOOL_NAMES
