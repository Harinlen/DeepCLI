"""StandardOrchestrator facade and compatibility exports."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

from kernel.llm.config import ModelRef
from kernel.llm.types import Message, TextContent
from kernel.orchestrator import OrchestratorConfig, OrchestratorConfigPatch
from kernel.orchestrator.agents import drain_orphan_notifications, make_spawn_subagent
from kernel.orchestrator.compactor import Compactor
from kernel.orchestrator.constants import (
    COMPACTION_FRACTION as _COMPACTION_FRACTION,
    DEFAULT_CONTEXT_WINDOW as _DEFAULT_CONTEXT_WINDOW,
    MAX_OUTPUT_TOKEN_RETRIES as _MAX_OUTPUT_TOKEN_RETRIES,
    MAX_REACTIVE_RETRIES as _MAX_REACTIVE_RETRIES,
    MAX_TOKENS_ESCALATED as _MAX_TOKENS_ESCALATED,
    SUBAGENT_DEFAULT_MAX_TURNS as _SUBAGENT_DEFAULT_MAX_TURNS,
)
from kernel.orchestrator.events import OrchestratorEvent
from kernel.orchestrator.hooks import fire_query_hook
from kernel.orchestrator.history import ConversationHistory
from kernel.orchestrator.loop import run_query
from kernel.orchestrator.notifications import (
    format_monitor_notification as _format_monitor_notification,
)
from kernel.orchestrator.notifications import (
    format_task_notification as _format_task_notification,
)
from kernel.orchestrator.prompt_builder import PromptBuilder
from kernel.orchestrator.prompt_runtime import (
    build_session_guidance,
    dump_system_prompt as _dump_system_prompt,
    inject_plan_mode_prompts,
    inject_session_guidance,
)
from kernel.orchestrator.reminders import (
    drain_pending_reminders as _drain_pending_reminders,
)
from kernel.orchestrator.reminders import extract_text as _extract_text
from kernel.orchestrator.reminders import format_reminders as _format_reminders
from kernel.orchestrator.reminders import to_text_content as _to_text_content
from kernel.orchestrator.runtime import PermissionMode
from kernel.orchestrator.types import OrchestratorDeps, PermissionCallback, StopReason

logger = logging.getLogger(__name__)

VALID_PERMISSION_MODES: tuple[PermissionMode, ...] = (
    "default",
    "plan",
    "bypass",
    "accept_edits",
    "auto",
    "dont_ask",
)


class StandardOrchestrator:
    """Session-scoped implementation of the Orchestrator Protocol.

    The class owns one conversation history and delegates most behavior to
    focused helper modules.  SessionManager treats it as a streaming facade:
    prompts enter through ``query()``, state changes leave as OrchestratorEvent
    values, and all cross-subsystem access flows through ``OrchestratorDeps``.
    """

    def __init__(
        self,
        deps: OrchestratorDeps,
        session_id: str,
        initial_history: list[Any] | None = None,
        config: OrchestratorConfig | None = None,
        depth: int = 0,
        cwd: Path | None = None,
        agent_id: str | None = None,
    ) -> None:
        """Create a session-local conversation engine.

        Args:
            deps: Kernel subsystems and closures needed by the session runtime.
            session_id: Stable session identifier used for persistence and hooks.
            initial_history: Optional provider-neutral history restored on resume.
            config: Initial user-visible model/config snapshot.
            depth: Root/sub-agent depth; root sessions use ``0``.
            cwd: Initial working directory for tools and hooks.
            agent_id: Child-agent id when this orchestrator runs as a sub-agent.
        """
        self._deps = deps
        self._session_id = session_id
        self._depth = depth
        self._cwd = cwd or Path.cwd()
        self._agent_id = agent_id
        self._closed = False
        default_model = _default_model(deps)
        self._config = config or OrchestratorConfig(model=default_model, temperature=None)
        self._mode: PermissionMode = "default"
        self._stop_reason = StopReason.end_turn
        self._plan_mode_turn_count = 0
        self._plan_mode_attachment_count = 0
        self._has_exited_plan_mode = False
        self._needs_plan_mode_exit_attachment = False
        self._turn_input_tokens = 0
        self._turn_output_tokens = 0
        self._history = ConversationHistory(initial_messages=initial_history)
        self._prompt_builder = PromptBuilder(session_id=session_id, deps=deps)
        self._compactor = Compactor(deps=deps, model=self._config.model, keep_recent_turns=5)

    def query(
        self,
        prompt: list[Any],
        *,
        on_permission: PermissionCallback,
        token_budget: int | None = None,
        max_turns: int = 0,
    ) -> AsyncGenerator[OrchestratorEvent, None]:
        """Start a query turn.

        Args:
            prompt: User content blocks for this turn.
            on_permission: Callback used for interactive tool authorization.
            token_budget: Optional input+output token cap for this query.
            max_turns: Maximum model/tool loop iterations; ``0`` means unlimited.

        Returns:
            Async generator that yields OrchestratorEvent values in persistence
            order.
        """
        return self._run_query(
            prompt,
            on_permission=on_permission,
            token_budget=token_budget,
            max_turns=max_turns,
        )

    def append_user_context(self, text: str) -> Message:
        """Append user-originated context outside a model turn.

        Args:
            text: Context text to include in future LLM requests.

        Returns:
            The appended provider-neutral message.
        """
        self._history.append_user([TextContent(text=text)])
        return self._history.messages[-1]

    async def close(self) -> None:
        """Release resources held by the orchestrator.

        Returns:
            ``None``.  Calling this more than once is safe.
        """
        self._closed = True
        logger.debug("Orchestrator[%s]: closed", self._session_id)

    def set_mode(self, mode: str) -> None:
        """Switch permission mode.

        Args:
            mode: One of the supported ToolAuthorizer permission modes.

        Returns:
            ``None``.

        Raises:
            ValueError: If ``mode`` is not a known permission mode.
        """
        if mode not in VALID_PERMISSION_MODES:
            raise ValueError(f"Unknown mode: {mode!r}")
        self._mode = cast(PermissionMode, mode)

    def set_plan_mode(self, enabled: bool) -> None:
        """Toggle plan mode through the legacy boolean API.

        Args:
            enabled: ``True`` enters plan mode; ``False`` restores default mode.

        Returns:
            ``None``.
        """
        self._mode = "plan" if enabled else "default"

    def set_config(self, patch: OrchestratorConfigPatch) -> None:
        """Apply a partial user-visible config update.

        Args:
            patch: Config fields to update; ``None`` fields leave existing values.

        Returns:
            ``None``.  The compactor is rebuilt when model config changes so
            future summaries use the current compact/default model routing.
        """
        self._config = _merge_config_patch(self._config, patch)
        self._compactor = Compactor(deps=self._deps, model=self._config.model)

    @property
    def mode(self) -> str:
        """Current permission mode string.

        Returns:
            Mode id consumed by Session and ToolAuthorizer.
        """
        return self._mode

    @property
    def plan_mode(self) -> bool:
        """Whether plan mode is active.

        Returns:
            ``True`` when the current mode is ``"plan"``.
        """
        return self._mode == "plan"

    @property
    def config(self) -> OrchestratorConfig:
        """Current user-visible config.

        Returns:
            Immutable config snapshot safe for Session broadcasts.
        """
        return self._config

    @property
    def stop_reason(self) -> StopReason:
        """Stop reason from the most recent query.

        Returns:
            Last terminal reason recorded by the query loop.
        """
        return self._stop_reason

    @property
    def last_turn_usage(self) -> tuple[int, int]:
        """Input/output token usage for the last completed turn.

        Returns:
            ``(input_tokens, output_tokens)`` as reported by the provider when
            available, otherwise the query-loop estimate.
        """
        return (self._turn_input_tokens, self._turn_output_tokens)

    def _build_session_guidance(self, enabled_tools: set[str], has_skills: bool) -> str | None:
        """Build session-specific prompt guidance.

        Args:
            enabled_tools: Tool names visible for the current prompt snapshot.
            has_skills: Whether the skill subsystem has any available skills.

        Returns:
            Rendered guidance text, or ``None`` when no guidance applies.
        """
        return build_session_guidance(self, enabled_tools, has_skills)

    def _inject_session_guidance(
        self,
        system_prompt: list[Any],
        snapshot_tool_names: set[str],
    ) -> None:
        """Append session guidance to an in-progress system prompt.

        Args:
            system_prompt: Mutable prompt section list being built for a turn.
            snapshot_tool_names: Tool names visible in the turn's tool snapshot.

        Returns:
            ``None``.  The list is mutated in place.
        """
        inject_session_guidance(self, system_prompt, snapshot_tool_names)

    def _inject_plan_mode_prompts(self, system_prompt: list[Any]) -> None:
        """Append plan-mode reminders to an in-progress system prompt.

        Args:
            system_prompt: Mutable prompt section list being built for a turn.

        Returns:
            ``None``.  The list is mutated in place when reminders apply.
        """
        inject_plan_mode_prompts(self, system_prompt)

    def _plan_file_path(self) -> str:
        """Return the durable plan file path for this session.

        Returns:
            Absolute or user-home-relative path produced by the plan subsystem.
        """
        from kernel.plans import get_plan_file_path

        return str(get_plan_file_path(self._session_id))

    async def _run_query(
        self,
        prompt: list[Any],
        *,
        on_permission: PermissionCallback,
        token_budget: int | None = None,
        max_turns: int = 0,
    ) -> AsyncGenerator[OrchestratorEvent, None]:
        """Run the delegated query loop.

        Args:
            prompt: User content blocks for this turn.
            on_permission: Callback used for interactive tool authorization.
            token_budget: Optional input+output token cap for this query.
            max_turns: Maximum model/tool loop iterations; ``0`` means unlimited.

        Yields:
            OrchestratorEvent values from the helper query loop.
        """
        async for event in run_query(
            self,
            prompt,
            on_permission=on_permission,
            token_budget=token_budget,
            max_turns=max_turns,
        ):
            yield event

    def _compaction_threshold(self) -> int:
        """Return the token threshold that triggers compaction.

        Returns:
            Integer token threshold derived from the cached context window and
            the compaction safety fraction.
        """
        if not hasattr(self, "_cached_context_window"):
            self._cached_context_window = _DEFAULT_CONTEXT_WINDOW
        return int(self._cached_context_window * _COMPACTION_FRACTION)

    async def _fire_hook(
        self,
        *,
        event: Any,
        user_text: str | None = None,
        message_count: int | None = None,
        token_estimate: int | None = None,
        stop_reason: str | None = None,
    ) -> tuple[bool, Any]:
        """Fire a query-level hook through HookManager.

        Args:
            event: Hook event to fire.
            user_text: Prompt text for user-prompt hooks.
            message_count: Current history length for stop hooks.
            token_estimate: Current token estimate for stop hooks.
            stop_reason: Query stop reason for stop hooks.

        Returns:
            ``(blocked, ctx)`` from the hook subsystem.
        """
        return await fire_query_hook(
            deps=self._deps,
            session_id=self._session_id,
            cwd=self._cwd,
            depth=self._depth,
            mode=self._mode,
            event=event,
            user_text=user_text,
            message_count=message_count,
            token_estimate=token_estimate,
            stop_reason=stop_reason,
        )

    def _make_spawn_subagent(self) -> Any:
        """Build a ``spawn_subagent`` closure for ToolContext.

        Returns:
            Async closure used by AgentTool/SendMessageTool to run child agents.
        """
        return make_spawn_subagent(self)

    def _drain_orphan_notifications(self, ended_agent_id: str) -> None:
        """Drain task notifications left by a completed child agent.

        Args:
            ended_agent_id: Child-agent id whose queued notifications should be
                promoted to the parent session.

        Returns:
            ``None``.  Notifications are queued as reminders through deps.
        """
        drain_orphan_notifications(self, ended_agent_id)


def _default_model(deps: OrchestratorDeps) -> ModelRef:
    """Resolve the default model from provider deps.

    Args:
        deps: Orchestrator dependency bundle containing the provider.

    Returns:
        Provider-selected default model, or a stable placeholder for tests.
    """
    provider_model_for = getattr(deps.provider, "model_for", None)
    if callable(provider_model_for):
        return provider_model_for("default")
    return ModelRef(provider="default", model="default")


def _merge_config_patch(
    current: OrchestratorConfig,
    patch: OrchestratorConfigPatch,
) -> OrchestratorConfig:
    """Apply a partial config patch without mutating the old snapshot.

    Args:
        current: Existing config snapshot.
        patch: Partial update where ``None`` leaves a field unchanged.

    Returns:
        New config snapshot.
    """
    return OrchestratorConfig(
        model=patch.model if patch.model is not None else current.model,
        temperature=patch.temperature if patch.temperature is not None else current.temperature,
        streaming_tools=patch.streaming_tools
        if patch.streaming_tools is not None
        else current.streaming_tools,
        language=patch.language if patch.language is not None else current.language,
    )


__all__ = [
    "StandardOrchestrator",
    "_dump_system_prompt",
    "_extract_text",
    "_to_text_content",
    "_drain_pending_reminders",
    "_format_reminders",
    "_format_task_notification",
    "_format_monitor_notification",
    "_SUBAGENT_DEFAULT_MAX_TURNS",
    "_COMPACTION_FRACTION",
    "_DEFAULT_CONTEXT_WINDOW",
    "_MAX_REACTIVE_RETRIES",
    "_MAX_OUTPUT_TOKEN_RETRIES",
    "_MAX_TOKENS_ESCALATED",
]
