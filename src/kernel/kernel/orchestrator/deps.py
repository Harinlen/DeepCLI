"""Injected dependencies consumed by the Orchestrator runtime."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from kernel.hooks import HookManager
    from kernel.memory import MemoryManager
    from kernel.prompts.manager import PromptManager
    from kernel.schedule import ScheduleManager
    from kernel.skills import SkillManager
    from kernel.tasks.registry import TaskRegistry
    from kernel.tool_authz.authorizer import ToolAuthorizer
    from kernel.tools import ToolManager


@runtime_checkable
class LLMProvider(Protocol):
    """The slice of LLMManager that Orchestrator needs.

    Keeping this Protocol narrow lets tests provide simple fake providers and
    keeps Orchestrator independent from provider lifecycle management.
    """

    async def stream(
        self,
        *,
        system: list[Any],
        messages: list[Any],
        tool_schemas: list[Any],
        model: Any,
        temperature: float | None,
        thinking: bool = False,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[Any, None]:
        """Return an async generator of LLMChunk events for one model call.

        Args:
            system: Rendered system prompt sections.
            messages: Conversation history in provider-neutral LLM message form.
            tool_schemas: Visible tool schemas for this turn.
            model: Provider/model reference selected for the call.
            temperature: Optional sampling temperature override.
            thinking: Whether provider-specific thinking output is requested.
            max_tokens: Optional output cap used by retry escalation.

        Yields:
            Provider-neutral LLM chunks consumed by the query loop.

        Returns:
            Async generator object for the provider stream.
        """
        ...


@dataclass
class OrchestratorDeps:
    """All external dependencies for a session-scoped Orchestrator.

    The dataclass is intentionally dependency-injection heavy: Orchestrator is
    per-session runtime code, while most fields are kernel subsystems owned by
    application lifespan.  Optional fields allow degraded tests and feature flags
    without requiring alternate constructors.
    """

    # Required stream source; everything else can degrade or be feature-gated.
    provider: LLMProvider
    # Tool registry snapshot source for visible schemas and lookup.
    tool_source: ToolManager | None = field(default=None)
    # Centralized policy gate.  Missing authorizer degrades to allow with warning.
    authorizer: ToolAuthorizer | None = field(default=None)
    # Authentication context is opaque here; ToolAuthorizer owns interpretation.
    connection_auth: Any = field(default=None)
    # Dynamic guard for non-interactive sessions such as resumed gateway tasks.
    should_avoid_prompts_provider: Callable[[], bool] | None = field(default=None)
    memory: MemoryManager | None = field(default=None)
    skills: SkillManager | None = field(default=None)
    hooks: HookManager | None = field(default=None)
    # Session-layer mode setter also broadcasts ACP updates.
    set_mode: Callable[[str], None] | None = field(default=None)
    # Reminder closures bridge async hooks/tasks into the next user turn.
    queue_reminders: Callable[[list[str]], None] | None = field(default=None)
    drain_reminders: Callable[[], list[str]] | None = field(default=None)
    prompts: PromptManager | None = field(default=None)
    task_registry: TaskRegistry | None = field(default=None)
    # Cross-session delivery remains a SessionManager responsibility.
    deliver_cross_session: Callable[[str, str], bool] | None = field(default=None)
    # Durable peer-agent message delivery is routed through Agent Hub.Router.
    route_agent_message: Callable[[str, str], bool] | None = field(default=None)
    schedule_manager: ScheduleManager | None = field(default=None)
    git: Any = field(default=None)
    # Narrow summarisation helper used by tools that need a secondary LLM pass.
    summarise: Callable[[str, str], Any] | None = field(default=None)
    # Volatile MCP prompt instructions are loaded per turn, not cached globally.
    mcp_instructions: Callable[[], list[tuple[str, str]]] | None = field(default=None)
    mcp: Any = field(default=None)
