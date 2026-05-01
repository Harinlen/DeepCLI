"""ToolContext — the single channel through which a Tool touches the kernel.

Design rules (see [`docs/plans/landed/tool-manager.md`](../../../docs/plans/landed/tool-manager.md) § 5):

1. Tool must **only** access kernel state via ``ToolContext``.  No module
   imports of SessionManager / HookManager / ToolAuthorizer from inside
   ``Tool.call``.
2. Mutations to session-level state (cwd, env, worktree path, …) go
   through ``ToolCallResult.context_modifier`` — a pure function applied
   by the Orchestrator — **not** by directly mutating the context passed
   in.
3. No ``authorizer_hint`` field (aligned with Claude Code — Tools don't
   pre-check permissions; all authorization happens at the ToolExecutor
   layer via ToolAuthorizer).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from kernel.orchestrator.events import OrchestratorEvent
    from kernel.protocol.interfaces.contracts.content_block import ContentBlock
    from kernel.tasks.registry import TaskRegistry
    from kernel.tools.file_state import FileStateCache


@dataclass
class ToolContext:
    """Per-tool-call context injected by the Orchestrator.

    Narrower than Claude Code's ``ToolUseContext`` — only the fields a
    Tool *actually* needs.  Missing from this context vs Claude Code:
    ``tools`` / ``mcpClients`` registry access (Tool doesn't know about
    the registry), ``renderedSystemPrompt`` (only Orchestrator needs it),
    ``contentReplacementState`` (compression layer), and
    ``authorizer_hint`` (Tool doesn't pre-check).
    """

    session_id: str
    """Session this tool call belongs to.  Sub-agents use a derived id."""

    agent_depth: int
    """``0`` = root agent, ``>=1`` = sub-agent."""

    agent_id: str | None
    """Unique id of the sub-agent, or ``None`` for the root agent.

    Used by event routing to separate sub-agent JSONL files.
    """

    cwd: Path
    """Current working directory for path-relative operations."""

    cancel_event: asyncio.Event
    """Long-running tools poll this between work units."""

    file_state: FileStateCache
    """Tools subsystem shared state — FileRead records, FileEdit verifies."""

    blobs: Any = None
    """Session ``BlobStore`` — spillover for large tool_results.  ``None``
    in Phase 1 (BlobStore not yet implemented)."""

    tasks: TaskRegistry | None = None
    """Session :class:`~kernel.tasks.registry.TaskRegistry` — background
    task tracking for ``BashTool run_in_background`` and ``AgentTool``
    background mode.  ``None`` when the task system is unavailable."""

    set_plan_mode: Callable[[bool], None] | None = None
    """Toggle plan mode on the Orchestrator.  Wired by ToolExecutor.
    Used by EnterPlanModeTool / ExitPlanModeTool.
    Deprecated: prefer ``set_mode("plan")`` / ``set_mode("restore")``."""

    set_mode: Callable[[str], None] | None = None
    """Switch permission mode.  Wired by ToolExecutor from
    ``OrchestratorDeps.set_mode`` (Session-layer closure that writes
    events + broadcasts).  Special values:
    - ``"plan"`` → enter plan mode (stores prePlanMode)
    - ``"restore"`` → exit plan mode and restore prePlanMode"""

    interactive: bool = True
    """Whether the session has an interactive UI for permission prompts.
    ``False`` when ``should_avoid_prompts=True`` (no WS connection).
    Used by EnterPlanModeTool to prevent entering plan mode in
    non-interactive sessions (ExitPlanMode requires user confirmation)."""

    queue_reminders: Callable[[list[str]], None] | None = None
    """Push system-reminder strings into the session's pending buffer.
    Used by background task notifications (stall watchdog, etc.).
    Wired by ToolExecutor from ``OrchestratorDeps.queue_reminders``."""

    spawn_subagent: (
        Callable[[str, list[ContentBlock]], AsyncGenerator[OrchestratorEvent, None]] | None
    ) = None
    """Orchestrator-provided closure that spawns a sub-agent as a nested
    query.  ``None`` when sub-agent spawning is not available (e.g. inside
    a depth-limited sub-agent itself, or before AgentTool is implemented).
    """

    deliver_cross_session: Callable[[str, str], bool] | None = None
    """Deliver a message to another session.  Signature:
    ``(target_session_id, message) -> success``.  Wired from
    ``SessionManager.deliver_message`` via ``OrchestratorDeps``.
    Used by ``SendMessageTool`` for ``to="session:<id>"`` addressing.
    ``None`` when cross-session messaging is unavailable."""

    route_agent_message: Callable[[str, str], bool] | None = None
    """Route a message to a durable Agent through Agent Hub.Router.

    Signature: ``(target_agent_id, message) -> success``.  This is for
    durable peer agents only; AgentTool children stay private to the owning
    session and are addressed through the task registry path above.
    """

    schedule_manager: Any = None
    """ScheduleManager subsystem instance — ``None`` when the schedule
    subsystem is disabled.  Used by CronCreate/Delete/List tools."""

    mcp_manager: Any = None
    """MCPManager subsystem instance — ``None`` when the MCP subsystem is
    disabled.  Used by ListMcpResourcesTool and ReadMcpResourceTool."""

    git_manager: Any = None
    """GitManager subsystem instance — ``None`` when the git subsystem
    is disabled.  Used by EnterWorktree/ExitWorktree tools."""

    env: dict[str, str] = field(default_factory=dict)
    """Environment variables applied by prior ``context_modifier`` tools
    (ActivateVenv, etc.).  Merges with ``os.environ`` at tool-call time."""

    summarise: Callable[[str, str], "Awaitable[str]"] | None = None
    """Narrow LLM-summarisation closure used by WebFetch secondary-model
    post-processing.  Signature: ``(content, user_prompt) -> str``.

    Wired by ToolExecutor from a Session-layer closure that resolves the
    ``compact`` role via ``LLMManager.model_for_or_default('compact')``
    and calls ``provider.stream()`` with the CC-style wrapper prompt.

    ``None`` when no LLM provider is available (rare; tests without a
    provider) — WebFetch falls back to returning raw content."""

    fire_hook: Callable[[Any, Any], "Awaitable[bool]"] | None = None
    """Fire a HookEvent with a pre-built ``HookEventCtx``.  Returns
    ``True`` when a handler raised ``HookBlock`` and the event's
    ``can_block`` spec is ``True``; ``False`` otherwise.  Signature:
    ``(event: HookEvent, ctx: HookEventCtx) -> bool``.

    Used by EnterWorktreeTool / ExitWorktreeTool for the non-git
    fallback path (CC's ``WorktreeCreate`` / ``WorktreeRemove`` hooks).
    ``None`` when the hook subsystem is unavailable — tools surface a
    clear error in that case."""


__all__ = ["ToolContext"]
