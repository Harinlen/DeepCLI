"""Structural protocols for Orchestrator helper modules."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, Protocol

from kernel.llm.types import PromptSection
from kernel.orchestrator.config import OrchestratorConfig
from kernel.orchestrator.deps import OrchestratorDeps
from kernel.orchestrator.events import OrchestratorEvent
from kernel.orchestrator.history import ConversationHistory
from kernel.orchestrator.types import PermissionCallback, StopReason
from kernel.tool_authz import PermissionMode


class QueryRuntime(Protocol):
    """The StandardOrchestrator shape consumed by query-loop helpers."""

    _deps: OrchestratorDeps
    _session_id: str
    _depth: int
    _cwd: Path
    _agent_id: str | None
    _config: OrchestratorConfig
    _mode: PermissionMode
    _stop_reason: StopReason
    _turn_input_tokens: int
    _turn_output_tokens: int
    _history: ConversationHistory
    _prompt_builder: Any
    _compactor: Any
    _plan_mode_turn_count: int
    _plan_mode_attachment_count: int
    _has_exited_plan_mode: bool
    _needs_plan_mode_exit_attachment: bool

    @property
    def plan_mode(self) -> bool:
        """Whether the runtime is currently using plan-mode prompts/tools.

        Returns:
            ``True`` when plan mode is active.
        """
        ...

    def set_mode(self, mode: str) -> None:
        """Switch the permission mode using the public Orchestrator semantics.

        Args:
            mode: Permission mode id.

        Returns:
            ``None``.
        """
        ...

    def set_plan_mode(self, enabled: bool) -> None:
        """Legacy boolean wrapper used by plan-mode tools.

        Args:
            enabled: ``True`` enters plan mode; ``False`` leaves it.

        Returns:
            ``None``.
        """
        ...

    def _compaction_threshold(self) -> int:
        """Return the token threshold that triggers compaction.

        Returns:
            Token count threshold for the current model/context window.
        """
        ...

    def _make_spawn_subagent(self) -> Any:
        """Build the child-agent spawn closure for ToolContext.

        Returns:
            Async spawn closure or compatible callable.
        """
        ...

    async def _fire_hook(
        self,
        *,
        event: Any,
        user_text: str | None = None,
        message_count: int | None = None,
        token_estimate: int | None = None,
        stop_reason: str | None = None,
    ) -> tuple[bool, Any]:
        """Fire a query-level hook.

        Args:
            event: Hook event to fire.
            user_text: Optional user prompt text.
            message_count: Optional conversation length.
            token_estimate: Optional token estimate.
            stop_reason: Optional query stop reason.

        Returns:
            ``(blocked, hook_context)``.
        """
        ...


class PlanPromptRuntime(QueryRuntime, Protocol):
    """Additional plan-mode state used by dynamic prompt injection."""


class SubAgentParentRuntime(QueryRuntime, Protocol):
    """Parent runtime shape needed to spawn child orchestrators."""

    def query(
        self,
        prompt: list[Any],
        *,
        on_permission: PermissionCallback,
        token_budget: int | None = None,
        max_turns: int = 0,
    ) -> AsyncGenerator[OrchestratorEvent, None]:
        """Run a child query and stream its Orchestrator events.

        Args:
            prompt: Child prompt content blocks.
            on_permission: Permission callback inherited from the parent call.
            token_budget: Optional child token budget.
            max_turns: Child loop cap.

        Returns:
            Async generator object for the child event stream.

        Yields:
            Events emitted by the child StandardOrchestrator.
        """
        ...


def system_reminder_section(text: str) -> PromptSection:
    """Wrap text in the XML block used for reminder prompt sections.

    Args:
        text: Reminder body.

    Returns:
        Non-cacheable prompt section containing the reminder XML block.
    """
    return PromptSection(text=f"<system-reminder>\n{text}\n</system-reminder>", cache=False)
