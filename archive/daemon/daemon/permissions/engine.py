"""Runtime permission engine — combines rules, mode, and tool level.

The :class:`PermissionEngine` is consulted by
:class:`~daemon.engine.orchestrator.Orchestrator` before every
tool execution.  It produces a :class:`PermissionDecision`:

* ``ALLOW`` — run the tool without prompting.
* ``DENY`` — do not run; surface an error to the LLM.
* ``PROMPT`` — send a :class:`PermissionRequest` to the client and
  wait for the user's decision.

Evaluation order (first match wins)::

    BYPASS mode            → ALLOW
    deny rule matches      → DENY   (deny always beats allow)
    allow rule matches     → ALLOW
    PLAN mode              → ALLOW only read-only + plan-file edits
    ACCEPT_EDITS mode      → ALLOW file_write / file_edit
    tool.permission_level  → NONE ⇒ ALLOW, else PROMPT
                             (PROMPT mode default)
"""

from __future__ import annotations

import enum
import os
from pathlib import Path, PurePath
from typing import Any

from daemon.extensions.tools.base import PermissionLevel, Tool
from daemon.permissions.modes import PermissionMode, is_read_only_tool
from daemon.permissions.rules import matches as rule_matches
from daemon.permissions.settings import PermissionSettings


class PermissionDecision(enum.Enum):
    """Outcome of :meth:`PermissionEngine.check`."""

    ALLOW = "allow"
    DENY = "deny"
    PROMPT = "prompt"


# Tools whose invocation mutates files on disk.  ``ACCEPT_EDITS``
# mode auto-approves these; ``PLAN`` mode denies them unless they
# target the active plan file.
_FILE_WRITE_TOOLS: frozenset[str] = frozenset({"file_write", "file_edit"})

# Tools allowed unconditionally while in PLAN mode, on top of the
# read-only set.  These are the only tools that can exit plan mode.
_PLAN_MODE_CONTROL_TOOLS: frozenset[str] = frozenset({"enter_plan_mode", "exit_plan_mode"})

# Memory directory guardrail (D17).  MemoryStore must be the sole
# writer to ``~/.mustang/memory/`` — if the LLM is allowed to
# file_edit / file_write inside it, the in-RAM cache drifts from
# disk and the index.md silently stops matching reality.  file_read
# is allowed (LLM needs to read memory bodies).
_MEMORY_PROTECTED_ROOT = (Path.home() / ".mustang" / "memory").resolve()
_MEMORY_PROTECTED_WRITE_TOOLS: frozenset[str] = frozenset({"file_write", "file_edit"})


class PermissionEngine:
    """Evaluates tool calls against user rules, mode, and tool level.

    Not thread-safe — one engine per session orchestrator.

    Args:
        settings: Loaded :class:`PermissionSettings` (allow/deny
            rules).  May be a shared instance; the engine only
            reads from it on ``check()`` and only writes to it via
            :meth:`add_allow_rule_from_suggestion`.
        mode: Initial permission mode.  Defaults to
            :attr:`PermissionMode.PROMPT`.
    """

    def __init__(
        self,
        settings: PermissionSettings,
        mode: PermissionMode = PermissionMode.PROMPT,
    ) -> None:
        self._settings = settings
        self._mode = mode
        # Consecutive denial counter per tool — resets on any allow.
        # Used only for the user-facing "consider Always Allow" hint;
        # the engine itself never auto-disables a tool.
        self._denial_counts: dict[str, int] = {}
        # Plan file path registered by enter_plan_mode, checked by
        # plan mode to allow file_write/file_edit on that specific
        # file.  Cleared on exit_plan_mode.
        self._plan_file_path: str | None = None

    # -- Mode ---------------------------------------------------------

    @property
    def mode(self) -> PermissionMode:
        """Current permission mode."""
        return self._mode

    @mode.setter
    def mode(self, value: PermissionMode) -> None:
        self._mode = value

    @property
    def settings(self) -> PermissionSettings:
        """Backing settings store (for callers that need to persist)."""
        return self._settings

    # -- Plan file tracking ------------------------------------------

    def set_plan_file(self, path: str | None) -> None:
        """Register or clear the active plan-file path.

        When set, ``PLAN`` mode additionally allows ``file_write`` /
        ``file_edit`` **targeting that exact file**.  This lets the
        LLM incrementally update its plan while exploring.
        """
        self._plan_file_path = path

    # -- Decision ----------------------------------------------------

    def check(self, tool: Tool, tool_input: dict[str, Any]) -> PermissionDecision:
        """Produce a decision for a tool call.

        Args:
            tool: The :class:`Tool` about to be executed.
            tool_input: The LLM-supplied arguments dict.

        Returns:
            :class:`PermissionDecision.ALLOW`, ``DENY``, or ``PROMPT``.
        """
        tool_name = tool.name

        # Hardcoded memory-dir guardrail — runs BEFORE bypass so
        # even in BYPASS mode the LLM cannot corrupt memory/ via
        # file_edit / file_write.  See D17.
        if _is_memory_write_violation(tool_name, tool_input):
            return PermissionDecision.DENY

        if self._mode == PermissionMode.BYPASS:
            return PermissionDecision.ALLOW

        # deny beats allow — scan deny list first.
        for rule in self._settings.deny_rules:
            if rule_matches(rule.tool_rule, tool_name, tool_input):
                return PermissionDecision.DENY

        # allow list.
        for rule in self._settings.allow_rules:
            if rule_matches(rule.tool_rule, tool_name, tool_input):
                return PermissionDecision.ALLOW

        # Mode-specific fast paths.
        if self._mode == PermissionMode.PLAN:
            return self._check_plan_mode(tool_name, tool_input)

        if self._mode == PermissionMode.ACCEPT_EDITS and tool_name.lower() in _FILE_WRITE_TOOLS:
            return PermissionDecision.ALLOW

        # Fall through to tool-level classification (PROMPT mode
        # default, or ACCEPT_EDITS for non-file-write tools).
        # Use dynamic permission level to support per-invocation
        # classification (e.g. BashTool read-only commands).
        effective_level = tool.get_permission_level(tool_input)
        if effective_level == PermissionLevel.NONE:
            return PermissionDecision.ALLOW

        return PermissionDecision.PROMPT

    def _check_plan_mode(self, tool_name: str, tool_input: dict[str, Any]) -> PermissionDecision:
        """Plan-mode specialization: read-only tools + plan file only."""
        lower = tool_name.lower()

        if is_read_only_tool(lower) or lower in _PLAN_MODE_CONTROL_TOOLS:
            return PermissionDecision.ALLOW

        # Permit edits targeting the registered plan file.
        if lower in _FILE_WRITE_TOOLS and self._plan_file_path:
            target = tool_input.get("file_path") or tool_input.get("path") or ""
            if isinstance(target, str) and _same_path(target, self._plan_file_path):
                return PermissionDecision.ALLOW

        return PermissionDecision.DENY

    # -- Denial tracking ---------------------------------------------

    def record_denial(self, tool_name: str) -> int:
        """Increment the consecutive-denial counter for *tool_name*.

        Returns:
            The new consecutive count.  Callers use the value to
            decide whether to append a "consider Always Allow" hint
            to the tool-denied error message (≥ 3).
        """
        current = self._denial_counts.get(tool_name, 0) + 1
        self._denial_counts[tool_name] = current
        return current

    def record_allow(self, tool_name: str) -> None:
        """Reset the consecutive-denial counter for *tool_name*."""
        self._denial_counts.pop(tool_name, None)

    # -- Rule generation ---------------------------------------------

    def generate_rule_for_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> str:
        """Build a rule string suitable for "Always Allow".

        Heuristics (inspired by Claude Code's BashPermissionRequest):

        * **Bash** — extract the first token of ``command`` →
          ``"Bash(<token> *)"`` so subsequent invocations with other
          arguments also match.  Falls back to ``"Bash"`` when the
          command is empty.
        * **file_write / file_edit** — take the parent directory of
          ``file_path`` and produce ``"file_write(<dir>/**)"``.
          Falls back to the bare tool name when no path is present.
        * **Any other tool** — use the bare tool name, matching every
          invocation of that tool.

        The generated string is always parseable by
        :func:`~daemon.permissions.rules.parse_rule`.
        """
        lower = tool_name.lower()

        if lower == "bash":
            command = tool_input.get("command", "")
            if isinstance(command, str) and command.strip():
                first_token = command.strip().split()[0]
                return f"Bash({first_token} *)"
            return "Bash"

        if lower in _FILE_WRITE_TOOLS:
            path_val = tool_input.get("file_path") or tool_input.get("path")
            if isinstance(path_val, str) and path_val:
                parent = PurePath(path_val).parent.as_posix()
                # "." means current dir — pattern "**" covers all files.
                pattern = "**" if parent in ("", ".") else f"{parent}/**"
                return f"{lower}({pattern})"
            return lower

        return lower


def _is_memory_write_violation(tool_name: str, tool_input: dict[str, Any]) -> bool:
    """Return True iff the call is a write tool targeting a memory directory.

    Protected roots:
    - ``~/.mustang/memory/`` (global, cross-project)
    - ``<any>/.mustang/memory/`` (project-local, Phase 5.7C)

    Accepts any path form the LLM might pass (absolute, tilde, relative
    to cwd) and normalises via ``Path.expanduser().resolve()``.
    """
    if tool_name.lower() not in _MEMORY_PROTECTED_WRITE_TOOLS:
        return False

    raw = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(raw, str) or not raw:
        return False

    try:
        candidate = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        return False

    # Check global memory root.
    try:
        candidate.relative_to(_MEMORY_PROTECTED_ROOT)
        return True
    except ValueError:
        pass

    # Check project-local memory path (any .mustang/memory/ ancestor).
    for parent in candidate.parents:
        if parent.name == "memory" and parent.parent.name == ".mustang":
            return True

    return False


def _same_path(a: str, b: str) -> bool:
    """Compare two paths by normalised absolute form.

    Plan-file matching must tolerate differences between relative
    and absolute forms the LLM may pass versus what the daemon
    registered.  We normalise with :func:`os.path.normpath` and
    :func:`os.path.abspath` before comparison.
    """
    return os.path.abspath(a) == os.path.abspath(b)
