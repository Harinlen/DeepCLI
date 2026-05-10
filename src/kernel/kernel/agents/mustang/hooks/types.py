"""Public hook types — events, context, spec, exception.

See ``docs/plans/landed/hook-manager.md`` for the full design.

Design highlights:
- In-process Python handler (no subprocess) — trusted local code.
- Mutation-style side effects (OpenClaw): handler modifies the
  passed ``HookEventCtx`` directly; caller reads back after ``fire``.
- ``raise HookBlock("reason")`` is the single channel for blocking;
  whether it actually blocks depends on ``EVENT_SPECS[event].can_block``.
- ``HookEventCtx.messages`` is the system_reminder buffer — handlers
  ``append`` strings; the caller drains and queues into Session.
"""

from __future__ import annotations

import enum
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


class HookEvent(enum.Enum):
    """Lifecycle events that hooks can subscribe to.

    18 events in total: 16 aligned with the daemon-era ``5.5.4E``
    design, plus 2 worktree events mirroring Claude Code's
    ``WorktreeCreate`` / ``WorktreeRemove`` hooks (consulted by
    EnterWorktree / ExitWorktree when git is unavailable, for
    VCS-agnostic worktree isolation).  Claude Code defines 27+ events
    but most of the remaining ones are out-of-scope for the mustang
    framework-only stance.
    """

    # Tool lifecycle (fired by Orchestrator.ToolExecutor)
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    POST_TOOL_FAILURE = "post_tool_failure"

    # Session lifecycle (fired by SessionManager)
    SESSION_START = "session_start"
    SESSION_END = "session_end"

    # Conversation lifecycle (fired by Orchestrator)
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    POST_SAMPLING = "post_sampling"
    STOP = "stop"

    # Compaction (fired by Orchestrator)
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"

    # File events (fired by writer Tools, relayed by ToolExecutor)
    FILE_CHANGED = "file_changed"

    # Subagent lifecycle (fired by Orchestrator)
    SUBAGENT_START = "subagent_start"

    # Permission events (fired by ToolAuthorizer; pure audit)
    PERMISSION_REQUESTED = "permission_requested"
    PERMISSION_DENIED = "permission_denied"

    # Cron lifecycle (fired by CronExecutor)
    PRE_CRON_FIRE = "pre_cron_fire"
    POST_CRON_FIRE = "post_cron_fire"

    # Worktree lifecycle (fired by EnterWorktreeTool / ExitWorktreeTool
    # when git is not available — handlers provide VCS-agnostic
    # worktree isolation for non-git projects).
    WORKTREE_CREATE = "worktree_create"
    WORKTREE_REMOVE = "worktree_remove"


class HookBlock(Exception):
    """Raised by a handler to veto the current event.

    Only honoured when ``EVENT_SPECS[event].can_block`` is ``True``
    (currently: ``pre_tool_use``, ``user_prompt_submit``, ``pre_compact``).
    Raised on any other event the framework logs a warning and
    continues — never silently corrupts main flow.

    Plain ``Exception`` raised by a handler is treated as a bug:
    logged with traceback and the next handler runs (fail-open).
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class AmbientContext:
    """Shared ambient state that every hook can see.

    Frozen — caller fills once at fire-site construction time and the
    handler is not expected to modify any of these fields.  Field set
    is intentionally aligned with ``ToolAuthorizer.AuthorizeContext``
    so hook authors carry one mental model across both subsystems.
    """

    session_id: str
    cwd: Path
    agent_depth: int  # 0 = root agent, >=1 = subagent depth
    mode: Literal["default", "plan", "bypass", "accept_edits", "auto", "dont_ask"]
    timestamp: float


@dataclass
class HookEventCtx:
    """Mutable per-fire payload (OpenClaw-style mutation).

    The handler signature is ``(ctx: HookEventCtx) -> None`` (sync or
    async).  All side effects flow through field mutation:

    - ``tool_input`` / ``user_text`` — rewrite point for tool/prompt
      input (only honoured on events with
      ``EVENT_SPECS[event].accepts_input_mutation == True``).
    - ``messages`` — system_reminder buffer; handlers ``.append(...)``
      and the caller drains the list after ``fire`` returns.

    Caller ownership:
      Mutation of ``tool_input`` / ``user_text`` does **not** rewrite
      the audit trail.  Tool-use / user-prompt entries are appended to
      the SessionManager JSONL conversation history *before* hooks
      fire, so the on-disk audit is frozen by the time a handler sees
      the ctx.  The mutation is intentional and only affects downstream
      consumers in the current turn.

      After ``fire`` returns, ``ctx.messages`` is logically owned by
      the caller's drain path (typically
      ``session.queue_reminders(ctx.messages)``).  Do not continue
      mutating the list after handing it off.
    """

    event: HookEvent
    ambient: AmbientContext

    # Tool-related fields (set by ToolExecutor / ToolAuthorizer)
    tool_name: str | None = None
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_output: str | None = None
    error_message: str | None = None

    # User prompt (set by Orchestrator on user_prompt_submit)
    user_text: str | None = None

    # Compaction (set by Orchestrator)
    message_count: int | None = None
    token_estimate: int | None = None

    # File (set by FileWrite / FileEdit tools)
    file_path: str | None = None
    change_type: str | None = None  # "edit" | "write"

    # Session (set by SessionManager on session_start only)
    is_resume: bool | None = None

    # Stop (set by Orchestrator on stop)
    stop_reason: str | None = None

    # Subagent (set by Orchestrator on subagent_start only)
    agent_description: str | None = None

    # Worktree (set by EnterWorktreeTool / ExitWorktreeTool on the
    # WORKTREE_CREATE / WORKTREE_REMOVE events).  Handlers read
    # ``worktree_slug`` / ``worktree_path`` and write the created path
    # back via ``worktree_path`` (CREATE) or acknowledge the removal
    # (REMOVE).
    worktree_slug: str | None = None
    worktree_path: str | None = None
    worktree_handled: bool = False

    # System-reminder buffer — append from handlers, drain from caller.
    messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HookEventSpec:
    """Static per-event semantics consulted by HookManager at fire time.

    Kept minimal: only the two pieces of behaviour that vary per event.
    ``messages`` (the system_reminder buffer) is universally honoured
    so it does not appear here.
    """

    can_block: bool
    """If True, ``HookBlock`` raised by a handler aborts the event and
    ``HookManager.fire()`` returns ``True``.  Otherwise the exception
    is logged and the next handler still runs."""

    accepts_input_mutation: bool
    """Documentation hint for hook authors.  The framework does not
    enforce this — caller-side code reads ``ctx.tool_input`` /
    ``ctx.user_text`` and decides whether to honour the rewrite."""


# Per-event behaviour table.  Adding a new HookEvent without an entry
# here is a programming error; HookManager.fire() raises KeyError.
EVENT_SPECS: dict[HookEvent, HookEventSpec] = {
    HookEvent.PRE_TOOL_USE: HookEventSpec(can_block=True, accepts_input_mutation=True),
    HookEvent.POST_TOOL_USE: HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.POST_TOOL_FAILURE: HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.USER_PROMPT_SUBMIT: HookEventSpec(can_block=True, accepts_input_mutation=True),
    HookEvent.POST_SAMPLING: HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.PRE_COMPACT: HookEventSpec(can_block=True, accepts_input_mutation=False),
    HookEvent.POST_COMPACT: HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.SESSION_START: HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.SESSION_END: HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.STOP: HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.SUBAGENT_START: HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.PERMISSION_REQUESTED: HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.PERMISSION_DENIED: HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.FILE_CHANGED: HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.PRE_CRON_FIRE: HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.POST_CRON_FIRE: HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.WORKTREE_CREATE: HookEventSpec(can_block=False, accepts_input_mutation=False),
    HookEvent.WORKTREE_REMOVE: HookEventSpec(can_block=False, accepts_input_mutation=False),
}


# A handler is a sync or async callable taking the ctx.  ``iscoroutine``
# on the result decides whether the framework awaits.
HookHandler = Callable[[HookEventCtx], Awaitable[None] | None]


__all__ = [
    "AmbientContext",
    "EVENT_SPECS",
    "HookBlock",
    "HookEvent",
    "HookEventCtx",
    "HookEventSpec",
    "HookHandler",
]
