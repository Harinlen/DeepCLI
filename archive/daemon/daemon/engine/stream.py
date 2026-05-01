"""Universal stream event types — provider-agnostic.

Every provider translates its native response format into these events.
The engine and clients only ever see these types.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, Field

from daemon.tasks.store import TaskItem

if TYPE_CHECKING:
    from daemon.providers.base import ImageContent


class ThinkingDelta(BaseModel):
    """Incremental thinking/reasoning chunk from the LLM.

    Models like Qwen3.5 emit reasoning tokens separately from the
    final response.  Clients can choose to display or hide these.
    """

    type: Literal["thinking_delta"] = "thinking_delta"
    content: str


class TextDelta(BaseModel):
    """Incremental text chunk from the LLM."""

    type: Literal["text_delta"] = "text_delta"
    content: str


class ToolCallStart(BaseModel):
    """LLM is invoking a tool."""

    type: Literal["tool_call_start"] = "tool_call_start"
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]


class ToolCallResult(BaseModel):
    """Result of a tool execution, sent back to the LLM."""

    type: Literal["tool_call_result"] = "tool_call_result"
    tool_call_id: str
    tool_name: str
    output: str
    is_error: bool = False
    output_type: str = "text"
    """Hint for CLI rendering: ``"text"`` | ``"diff"`` | ``"file_content"`` | ``"command_output"``."""
    file_path: str | None = None
    """Source file path (for ``diff`` / ``file_content`` output types)."""
    exit_code: int | None = None
    """Process exit code (for ``command_output`` output type)."""
    image_parts: list["ImageContent"] | None = None
    """Image attachments produced by the tool (e.g. browser screenshot).

    Lightweight metadata only — ``data_base64`` is stripped to ``""`` by
    :meth:`tool_executor._persist_image_parts` before serialization, and
    the actual bytes live at ``~/.mustang/cache/images/<sha256>.<ext>``
    on disk.  Clients (CLI / TUI) load the bytes from that cache rather
    than receiving them over the WebSocket.
    """


class PermissionRequest(BaseModel):
    """Daemon asks client to approve a tool invocation.

    The client must respond with a ``permission_response`` message
    containing the same ``request_id`` and a ``decision`` field:
    one of ``"allow"``, ``"deny"``, or ``"always_allow"``.  When
    ``"always_allow"`` is returned the daemon persists the
    ``suggested_rule`` into ``~/.mustang/settings.json`` so the
    tool will not prompt again.

    Attributes:
        request_id: Unique identifier for this prompt.
        tool_name: Tool the LLM is requesting to invoke.
        arguments: Tool call arguments (shown to the user).
        suggested_rule: Pre-built rule string the client can offer
            as an ``Always Allow`` choice.  ``None`` when no
            meaningful rule can be generated (e.g. empty bash
            command) — the CLI then hides the button and falls
            back to a two-choice prompt.
    """

    type: Literal["permission_request"] = "permission_request"
    request_id: str
    tool_name: str
    arguments: dict[str, Any]
    suggested_rule: str | None = None
    warning: str | None = None
    """Informational destructive-command warning (BashTool only).

    When set, the CLI should display this prominently in the
    permission prompt so the user is aware of the risk.  Does not
    affect the permission decision — the user still chooses.
    """


class ToolDefinition(BaseModel):
    """Tool definition passed to the LLM for function calling.

    Shared type used by both the tool registry and providers.
    Lives here (engine.stream) because it has no dependencies on
    provider or extension internals.
    """

    name: str
    description: str
    parameters: dict[str, Any]


class UsageInfo(BaseModel):
    """Token usage statistics.

    Attributes:
        cache_creation_tokens: Tokens used to create a new cache entry
            (Anthropic prompt caching).  Zero for providers without
            explicit caching.
        cache_read_tokens: Tokens served from cache (Anthropic prompt
            caching).  Zero for providers without explicit caching.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


class StreamEnd(BaseModel):
    """Signals the end of a response turn."""

    type: Literal["end"] = "end"
    usage: UsageInfo = Field(default_factory=UsageInfo)
    context_used_pct: float | None = None
    """Context window usage as 0.0–100.0 percentage.  ``None`` when unknown."""
    model_name: str | None = None
    """Display name of the active model (for CLI status line)."""


class StreamError(BaseModel):
    """An error occurred during streaming."""

    type: Literal["error"] = "error"
    message: str


class PlanModeChanged(BaseModel):
    """Plan mode entered or exited (Step 4.8).

    Broadcast to all session connections so they can update any
    status indicator (e.g. the CLI status line).
    """

    type: Literal["plan_mode_changed"] = "plan_mode_changed"
    active: bool
    """``True`` when plan mode was just entered, ``False`` on exit."""

    previous_mode: str
    """Enum value of the mode before the change (for exit UX)."""


class PermissionModeChanged(BaseModel):
    """Permission mode switched (Step 5.8).

    Emitted after a ``permission_mode_request`` is processed, so
    every connected client can refresh any mode indicator.  Distinct
    from :class:`PlanModeChanged` because that event only fires on
    enter/exit of PLAN — this one fires for every transition.
    """

    type: Literal["permission_mode_changed"] = "permission_mode_changed"
    mode: str
    """Resolved enum value (``default`` / ``accept_edits`` / ``plan`` / ``bypass``)."""

    previous_mode: str
    """Enum value before the transition."""


class TaskUpdate(BaseModel):
    """Task list changed (Step 4.9).

    Emitted after a successful :class:`TodoWriteTool` invocation.
    Carries the full task list so clients can render it directly
    without re-querying.
    """

    type: Literal["task_update"] = "task_update"
    tasks: list[TaskItem]


class CompactNotification(BaseModel):
    """Notifies the client that context compaction occurred.

    Sent by the orchestrator after a successful auto-compact or
    manual ``/compact``.  The CLI uses this to display a status
    message to the user.
    """

    type: Literal["compact"] = "compact"
    summary_preview: str
    """First ~200 characters of the compaction summary."""
    messages_summarized: int
    """How many messages were replaced by the summary."""
    strategy: str = "full"
    """Compaction strategy used: ``"snip"``, ``"micro"``, or ``"full"``."""
    tokens_freed: int = 0
    """Estimated tokens freed by this compaction step."""


class UserQuestion(BaseModel):
    """Sends structured questions to the client for user input.

    The LLM calls ``ask_user_question`` which triggers this event.
    The client renders options and sends back ``UserQuestionResponse``.
    """

    type: Literal["user_question"] = "user_question"
    request_id: str
    questions: list[dict[str, Any]]
    """List of question dicts: {question, options, multi_select}."""


class UserQuestionResponse(BaseModel):
    """Client's answer to a ``user_question`` event."""

    request_id: str
    answers: dict[str, Any]
    """Maps question text → selected label(s)."""


class AgentStart(BaseModel):
    """Marks the beginning of a sub-agent execution (Phase 5.2).

    The CLI can use this to indent or visually group the sub-agent's
    output.  All events between ``AgentStart`` and ``AgentEnd`` with
    the same ``agent_id`` belong to the sub-agent.
    """

    type: Literal["agent_start"] = "agent_start"
    agent_id: str
    """Unique identifier for this sub-agent run."""
    prompt: str
    """The task description given to the sub-agent."""
    description: str = ""
    """Short (3-5 word) summary for UI display."""


class AgentEnd(BaseModel):
    """Marks the end of a sub-agent execution (Phase 5.2)."""

    type: Literal["agent_end"] = "agent_end"
    agent_id: str
    """Matches the ``agent_id`` from the corresponding ``AgentStart``."""


# Discriminated union of all stream event types
StreamEvent = Annotated[
    ThinkingDelta
    | TextDelta
    | ToolCallStart
    | ToolCallResult
    | PermissionRequest
    | StreamEnd
    | StreamError
    | CompactNotification
    | PlanModeChanged
    | PermissionModeChanged
    | TaskUpdate
    | UserQuestion
    | AgentStart
    | AgentEnd,
    Field(discriminator="type"),
]


class PermissionResponse(BaseModel):
    """User's reply to a :class:`PermissionRequest`.

    Returned by the WS client via the permission callback.
    Separate from :class:`StreamEvent` because it travels
    client → daemon (not in the streaming output).

    Attributes:
        request_id: Echoes the originating request.
        decision: One of ``"allow"``, ``"deny"``, ``"always_allow"``.
    """

    request_id: str
    decision: Literal["allow", "deny", "always_allow"]
