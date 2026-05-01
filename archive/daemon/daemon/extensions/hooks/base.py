"""Hook data models — config, context, result, and enums.

Defines the internal representations used throughout the hook system.
``HookConfig`` is constructed from ``HookRuntimeConfig`` (the config
schema layer) during manager initialization.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class HookEvent(enum.Enum):
    """Events that can trigger hooks.

    Tool-lifecycle events:

    - ``PRE_TOOL_USE``: Before a tool executes (can block).
    - ``POST_TOOL_USE``: After a tool executes.
    - ``POST_TOOL_FAILURE``: After a tool raises an exception.

    Session-lifecycle events:

    - ``SESSION_START``: Session created or resumed.
    - ``SESSION_END``: Session closing (cleanup).
    - ``STOP``: After the LLM finishes its response.
    - ``USER_PROMPT_SUBMIT``: User message received, before engine
      starts (can block or rewrite).

    Compaction events:

    - ``PRE_COMPACT``: Before compaction runs.
    - ``POST_COMPACT``: After compaction completes.

    File events:

    - ``FILE_CHANGED``: After a file is written or edited.

    Agent events:

    - ``SUBAGENT_START``: Sub-agent spawned.

    Permission events:

    - ``PERMISSION_DENIED``: User denied a permission prompt.
    """

    # Tool lifecycle
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    POST_TOOL_FAILURE = "post_tool_failure"

    # Session lifecycle
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    STOP = "stop"
    USER_PROMPT_SUBMIT = "user_prompt_submit"

    # Compaction
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"

    # File
    FILE_CHANGED = "file_changed"

    # Agent
    SUBAGENT_START = "subagent_start"

    # Permission
    PERMISSION_DENIED = "permission_denied"


class HookType(enum.Enum):
    """Execution strategy for a hook.

    - ``COMMAND``: Run a shell command via subprocess.
    - ``PROMPT``: Evaluate input with an LLM.
    - ``HTTP``: POST to an external URL.
    """

    COMMAND = "command"
    PROMPT = "prompt"
    HTTP = "http"


@dataclass(frozen=True, slots=True)
class HookConfig:
    """Fully resolved hook definition (internal representation).

    Constructed from :class:`~daemon.config.schema.HookRuntimeConfig`
    by :meth:`~daemon.extensions.manager.ExtensionManager.load_hooks`.

    Attributes:
        event: Which event triggers this hook.
        type: Execution strategy.
        if_: Optional ``ToolName(pattern)`` condition string.
        command: Shell command (for ``COMMAND`` type).
        timeout: Max execution time in seconds.
        async_: If True, run in background without waiting.
        prompt_text: Prompt template (for ``PROMPT`` type).
        model: Optional model override (for ``PROMPT`` type).
        url: Target URL (for ``HTTP`` type).
        headers: HTTP headers (for ``HTTP`` type).
        body: HTTP body template (for ``HTTP`` type).
    """

    event: HookEvent
    type: HookType
    if_: str | None = None

    # command type
    command: str | None = None
    timeout: int = 30
    async_: bool = False

    # prompt type
    prompt_text: str | None = None
    model: str | None = None

    # http type
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None


@dataclass(frozen=True, slots=True)
class HookContext:
    """Runtime context passed to hook executors.

    Carries information about the triggering event.  Fields are
    optional; each event type populates the subset that makes sense.

    Tool fields (pre/post_tool_use, post_tool_failure):
        tool_name, tool_input, tool_output, error_message.

    Session fields (session_start, session_end):
        session_id, cwd, is_resume, duration_s.

    User prompt field (user_prompt_submit):
        user_text.

    Compaction fields (pre/post_compact):
        message_count, token_estimate, messages_removed, summary_tokens.

    File field (file_changed):
        file_path, change_type.

    Agent field (subagent_start):
        agent_description, depth.
    """

    # Tool context (existing)
    tool_name: str | None = None
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_output: str | None = None

    # Error context (post_tool_failure)
    error_message: str | None = None

    # Session context (session_start, session_end)
    session_id: str | None = None
    cwd: str | None = None
    is_resume: bool | None = None
    duration_s: float | None = None

    # User prompt context (user_prompt_submit)
    user_text: str | None = None

    # Compaction context (pre/post_compact)
    message_count: int | None = None
    token_estimate: int | None = None
    messages_removed: int | None = None
    summary_tokens: int | None = None

    # File context (file_changed)
    file_path: str | None = None
    change_type: str | None = None  # "edit" | "write"

    # Agent context (subagent_start)
    agent_description: str | None = None
    depth: int | None = None


@dataclass(frozen=True, slots=True)
class HookResult:
    """Outcome of running one or more hooks.

    Attributes:
        blocked: If True, the tool call should be prevented
            (meaningful for ``pre_tool_use`` and ``user_prompt_submit``).
        output: Combined output from hook execution (for logging).
        modified_input: Rewritten input dict (for ``pre_tool_use``)
            or ``{"user_text": "..."}`` (for ``user_prompt_submit``).
            Ignored if ``blocked`` is True.
        permission: ``"allow"`` or ``"deny"`` for permission hooks.
    """

    blocked: bool = False
    output: str | None = None
    modified_input: dict[str, Any] | None = None
    permission: str | None = None
