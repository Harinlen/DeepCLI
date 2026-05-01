"""Orchestrator — drives the LLM conversation loop with tool execution.

One ``Orchestrator`` per session.  It is a thin coordination layer
that delegates to composed subsystems:

- :class:`ToolExecutor` — permission checks, hooks, tool dispatch
- :class:`Compactor` — context compaction
- :class:`MemoryManager` — global + project memory
- :class:`MemoryExtractor` — background memory extraction
- :class:`PlanModeController` — plan mode state machine
- :class:`SystemPromptBuilder` — system prompt assembly

The query loop lives here (migrated from the old ``_QueryLoopMixin``)
because it is the core coordination that wires the subsystems together.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, AsyncIterator

from daemon.config.schema import RuntimeConfig
from daemon.engine.conversation import Conversation
from daemon.engine.stream import (
    PermissionRequest,
    PermissionResponse,
    StreamEnd,
    StreamError,
    StreamEvent,
    TextDelta,
    ToolCallStart,
)
from daemon.errors import PromptTooLongError, ProviderError
from daemon.extensions.hooks.base import HookContext, HookEvent
from daemon.extensions.hooks.runner import run_hooks
from daemon.extensions.skills.registry import SkillRegistry
from daemon.extensions.tools.base import ToolContext
from daemon.extensions.tools.file_state_cache import FileStateCache
from daemon.permissions.engine import PermissionEngine
from daemon.providers.base import MessageContent, TextContent, ToolUseContent
from daemon.providers.registry import ProviderRegistry
from daemon.sessions.entry import AssistantMessageEntry, UserMessageEntry

from daemon.engine.orchestrator.compactor import Compactor
from daemon.engine.orchestrator.memory_extractor import MemoryExtractor
from daemon.engine.orchestrator.memory_manager import MemoryManager
from daemon.engine.orchestrator.plan_mode import PlanModeController
from daemon.engine.orchestrator.prompt_builder import SystemPromptBuilder
from daemon.engine.orchestrator.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

# Safety valve: max tool-loop iterations.
_MAX_TOOL_ROUNDS = 25

# Permission callback type.
PermissionCallback = Callable[[PermissionRequest], Awaitable[PermissionResponse]]


class Orchestrator:
    """Drives the LLM conversation loop with tool execution.

    Args:
        registry: Provider registry for model resolution.
        config: Resolved runtime configuration.
        conversation: Message history (fresh or resumed).
        tool_executor: Tool execution subsystem.
        compactor: Context compaction subsystem.
        memory_manager: Memory subsystem (or ``None``).
        memory_extractor: Background extraction (or ``None``).
        plan_mode: Plan mode controller.
        prompt_builder: System prompt assembler.
        skill_registry: Available skills.
        on_entry: Transcript writer callback.
        session_id: Session identifier.
        session_dir: Session directory.
        task_store: Per-session task store.
    """

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        config: RuntimeConfig,
        conversation: Conversation | None = None,
        tool_executor: ToolExecutor,
        compactor: Compactor,
        memory_manager: MemoryManager | None = None,
        memory_extractor: MemoryExtractor | None = None,
        plan_mode: PlanModeController,
        prompt_builder: SystemPromptBuilder,
        skill_registry: SkillRegistry | None = None,
        on_entry: Callable[[Any], None] | None = None,
        session_id: str | None = None,
        session_dir: Path | None = None,
        task_store: Any = None,
    ) -> None:
        self._registry = registry
        self._config = config
        self.conversation = conversation or Conversation()
        self.tool_executor = tool_executor
        self.compactor = compactor
        self.memory_manager = memory_manager
        self.memory_extractor = memory_extractor
        self.plan_mode = plan_mode
        self.prompt_builder = prompt_builder
        self._skill_registry = skill_registry or SkillRegistry()
        self._on_entry = on_entry
        self._session_id = session_id
        self._session_dir = session_dir
        self._task_store = task_store
        # Session-level provider override (from /model switch).
        self._provider_override: str | None = None
        # Per-session file state cache (stale-write prevention).
        self._file_state_cache = FileStateCache()
        # Background shell task manager.
        self._task_manager: Any = None  # TaskManager | None, lazy init
        # Sub-agent factory — set by SessionManager after construction.
        self.agent_factory: Any = None  # AgentFactory | None

    # -- Provider accessors ------------------------------------------------

    @property
    def effective_provider_name(self) -> str:
        """Active provider configuration name."""
        return self._provider_override or self._config.default_provider

    @property
    def effective_model(self) -> str | None:
        """Model string from the effective provider's config."""
        cfg = self._config.providers.get(self.effective_provider_name)
        return cfg.model if cfg else None

    def set_provider_override(self, name: str | None) -> None:
        """Set or clear the session-level provider override."""
        if name is not None and name not in self._config.providers:
            raise ValueError(f"Provider {name!r} not configured")
        self._provider_override = name

    def get_provider_snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of provider state."""
        providers_list = [
            {"name": name, "type": cfg.type, "model": cfg.model}
            for name, cfg in self._config.providers.items()
        ]
        return {
            "current_provider_name": self.effective_provider_name,
            "current_model": self.effective_model,
            "is_override": self._provider_override is not None,
            "default_provider_name": self._config.default_provider,
            "providers": providers_list,
        }

    # -- Transcript + git context ------------------------------------------

    def set_transcript_writer(self, writer: Callable[[Any], None] | None) -> None:
        """Install (or clear) the JSONL transcript-writer callback."""
        self._on_entry = writer

    def invalidate_git_status(self) -> None:
        """Force re-fetch of git status on next query."""
        self.prompt_builder.invalidate_git_status()

    # -- Conversation + public surface -------------------------------------

    async def clear(self) -> None:
        """Clear conversation history (``/clear`` command)."""
        await self.conversation.clear()

    @property
    def permission_engine(self) -> PermissionEngine:
        """Active permission engine."""
        return self.tool_executor.permission_engine

    @property
    def in_plan_mode(self) -> bool:
        """``True`` while the session is in plan mode."""
        return self.plan_mode.in_plan_mode

    async def enter_plan_mode(self) -> AsyncIterator[StreamEvent]:
        """Switch to plan mode (``/plan`` command)."""
        async for evt in self.plan_mode.enter():
            yield evt

    async def set_permission_mode(self, mode: Any) -> AsyncIterator[StreamEvent]:
        """Switch the active permission mode (Step 5.8)."""
        async for evt in self.plan_mode.set_mode(mode):
            yield evt

    async def exit_plan_mode(self, plan: str = "") -> AsyncIterator[StreamEvent]:
        """Exit plan mode (``/plan exit``)."""
        async for evt in self.plan_mode.exit(plan):
            yield evt

    async def force_compact(self) -> AsyncIterator[StreamEvent]:
        """Manually trigger compaction (``/compact``)."""
        provider_name = self.effective_provider_name
        provider = self._registry.get(provider_name)
        provider_cfg = self._config.providers.get(provider_name)
        model = provider_cfg.model if provider_cfg else None
        async for evt in self.compactor.force_compact(
            self.conversation,
            provider,
            model,
            self._on_entry,
            self.memory_manager,
        ):
            yield evt

    def current_tasks(self) -> list[dict[str, str]]:
        """Return the session's current task list."""
        if self._task_store is None:
            return []
        try:
            items = self._task_store.load()
        except Exception:
            logger.exception("Failed to load task list")
            return []
        return [t.model_dump() for t in items]

    @property
    def message_count(self) -> int:
        """Number of messages in the conversation."""
        return self.conversation.message_count

    async def drain_pending_extractions(self, timeout: float | None = None) -> None:
        """Await in-flight memory extractions (shutdown hook)."""
        if self.memory_extractor:
            await self.memory_extractor.drain(timeout)

    # -- Query loop --------------------------------------------------------

    async def query(
        self,
        user_text: str,
        permission_callback: PermissionCallback | None = None,
        ask_user: Any | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Process a user message and yield streaming events.

        Runs the full tool loop: LLM → tool calls → results → repeat
        until the LLM produces a final text response.
        """
        # Strip orphaned tool_use blocks from previous cancelled turns.
        removed = await self.conversation.strip_orphaned_tool_calls()
        if removed:
            logger.info("Stripped %d orphaned tool_use block(s)", removed)

        await self.conversation.add_user_message(user_text)
        if self._on_entry:
            self._on_entry(UserMessageEntry(content=user_text))

        # Resolve provider + model.
        provider_name = self.effective_provider_name
        provider = self._registry.get(provider_name)
        provider_cfg = self._config.providers.get(provider_name)
        model = provider_cfg.model if provider_cfg else None

        # Resolve provider-specific model identity (cutoff, family info).
        identity = provider.model_identity(model)
        self._knowledge_cutoff = identity.knowledge_cutoff if identity else None
        self._identity_lines = identity.identity_lines if identity else None

        # Lazy context-window resolution.
        if self.compactor.context_window <= 0:
            from daemon.engine.compact_types import resolve_context_window

            config_cw = provider_cfg.context_window if provider_cfg else None
            api_cw = await provider.query_context_window()
            self.compactor.context_window = resolve_context_window(config_cw, api_cw)
            logger.info("Context window resolved to %d tokens", self.compactor.context_window)
            if api_cw and not config_cw:
                from daemon.config.loader import update_provider_field

                update_provider_field(provider_name, "context_window", api_cw)

        # Lazy memory selector init.
        if self.memory_manager is not None:
            self.memory_manager.ensure_selector(provider)

        # Lazy git status fetch.
        if self.prompt_builder.git_status_needs_fetch:
            from daemon.utils.git import get_git_status

            self.prompt_builder.set_git_status(await get_git_status(self.prompt_builder._cwd))

        # Build skill info.
        skill_info = [
            (s.name, s.description, s.when_to_use) for s in self._skill_registry.list_all()
        ] or None

        # Lazy TaskManager init.
        if self._task_manager is None and self._session_dir is not None:
            from daemon.tasks.shell_task import TaskManager

            self._task_manager = TaskManager(session_dir=self._session_dir)

        tool_ctx = ToolContext(
            cwd=str(self.prompt_builder._cwd),
            memory_store=self.memory_manager.memory_store if self.memory_manager else None,
            project_memory_store=(
                self.memory_manager.project_memory_store if self.memory_manager else None
            ),
            file_state_cache=self._file_state_cache,
            ask_user=ask_user,
            task_manager=self._task_manager,
        )

        try:
            async for evt in self._run_tool_loop(
                provider=provider,
                model=model,
                skill_info=skill_info,
                tool_ctx=tool_ctx,
                permission_callback=permission_callback,
                user_text=user_text,
            ):
                yield evt
        finally:
            await asyncio.shield(
                self.tool_executor.finalize_cancelled_calls(self.conversation, self._on_entry)
            )

    async def _run_tool_loop(
        self,
        *,
        provider: Any,
        model: str | None,
        skill_info: list[tuple[str, str, str | None]] | None,
        tool_ctx: ToolContext,
        permission_callback: PermissionCallback | None,
        user_text: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Drive provider streaming + tool execution until the LLM is done."""
        reactive_retries = 0
        _MAX_REACTIVE_RETRIES = 2

        for _round in range(_MAX_TOOL_ROUNDS):
            # Collect background task notifications.
            task_notifications: list[str] = []
            if self._task_manager is not None:
                for completed in self._task_manager.collect_notifications():
                    tail = self._task_manager.read_output_tail(completed.id, 500)
                    note = f"Background task {completed.id} ({completed.command[:60]})"
                    if completed.status == "completed":
                        note += f" completed successfully (exit 0, {completed.elapsed:.1f}s)."
                    elif completed.status == "cancelled":
                        note += " was cancelled."
                    else:
                        note += f" failed (exit {completed.exit_code}, {completed.elapsed:.1f}s)."
                    if tail:
                        note += f"\nOutput (last 500 chars):\n{tail}"
                    task_notifications.append(note)

            # Auto-compact check.
            async for evt in self.compactor.compact_if_needed(
                self.conversation,
                provider,
                model,
                self._on_entry,
                self.memory_manager,
            ):
                yield evt

            # Build system prompt.
            round_user_text = user_text if _round == 0 else None
            lazy_names = self.tool_executor.tool_registry.lazy_tool_names or None
            system_prompt = await self.prompt_builder.build_for_round(
                model=model,
                model_id=model,
                knowledge_cutoff=self._knowledge_cutoff,
                identity_lines=self._identity_lines,
                skill_info=skill_info,
                memory_manager=self.memory_manager,
                plan_mode=self.plan_mode,
                user_message=round_user_text,
                lazy_tool_names=lazy_names,
                task_notifications=task_notifications or None,
            )

            messages = self.conversation.get_messages()
            text_parts: list[str] = []
            tool_calls: list[ToolUseContent] = []
            tool_defs = self.tool_executor.tool_registry.get_core_definitions()
            end_event: StreamEnd | None = None

            try:
                async for event in provider.stream(
                    messages=messages,
                    tools=tool_defs or None,
                    model=model,
                    system=system_prompt,
                ):
                    if isinstance(event, TextDelta):
                        text_parts.append(event.content)
                    elif isinstance(event, ToolCallStart):
                        tool_calls.append(
                            ToolUseContent(
                                tool_call_id=event.tool_call_id,
                                name=event.tool_name,
                                arguments=event.arguments,
                            )
                        )
                    if isinstance(event, StreamEnd):
                        end_event = event
                        if event.usage.input_tokens > 0:
                            self.compactor.state.last_known_input_tokens = event.usage.input_tokens
                        content = self._build_assistant_content(text_parts, tool_calls)
                        await self._record_assistant_message(content)
                        self._emit_assistant_entry(content, event)
                    else:
                        yield event
            except PromptTooLongError:
                if reactive_retries >= _MAX_REACTIVE_RETRIES:
                    logger.error("Context too large even after %d compactions", reactive_retries)
                    yield StreamError(message="Context too large even after compaction.")
                    yield StreamEnd()
                    return
                reactive_retries += 1
                logger.warning(
                    "Prompt too long — triggering reactive compaction (attempt %d/%d)",
                    reactive_retries,
                    _MAX_REACTIVE_RETRIES,
                )
                async for evt in self.compactor.reactive_compact(
                    self.conversation,
                    provider,
                    model,
                    self._on_entry,
                    self.memory_manager,
                ):
                    yield evt
                continue  # Retry this round after compaction.
            except ProviderError as exc:
                logger.error("Provider error during query: %s", exc)
                yield StreamError(message=str(exc))
                yield StreamEnd()
                return

            # No tool calls → done.
            if not tool_calls:
                await self._fire_stop_hooks()
                self._maybe_trigger_extract()
                if end_event is not None:
                    # Enrich with context stats for CLI status line.
                    cw = self.compactor.context_window
                    inp = self.compactor.state.last_known_input_tokens
                    if cw > 0 and inp > 0:
                        end_event.context_used_pct = round(inp / cw * 100, 1)
                    end_event.model_name = model or provider_name
                    yield end_event
                return

            # Plan + execute tool calls.
            groups = self.tool_executor.plan_groups(tool_calls)
            for group in groups:
                if len(group) == 1:
                    async for evt in self.tool_executor.execute(
                        group[0].tc,
                        tool_ctx,
                        self.conversation,
                        self._on_entry,
                        permission_callback,
                        execute_agent_call=self._execute_agent_call,
                    ):
                        yield evt
                else:
                    async for evt in self.tool_executor.execute_parallel_group(
                        group,
                        tool_ctx,
                        self.conversation,
                        self._on_entry,
                        permission_callback,
                        execute_agent_call=self._execute_agent_call,
                    ):
                        yield evt

        # Max rounds hit.
        logger.warning("Tool loop hit max rounds (%d)", _MAX_TOOL_ROUNDS)
        await self._fire_stop_hooks()
        self._maybe_trigger_extract()
        yield StreamError(message="Tool execution loop limit reached.")
        yield StreamEnd()

    # -- Agent execution (Phase 5.2) ---------------------------------------

    async def _execute_agent_call(
        self,
        tc: ToolUseContent,
        permission_callback: PermissionCallback | None,
    ) -> AsyncIterator[StreamEvent]:
        """Run a sub-agent and forward its events.

        Separated from ToolExecutor because it needs access to
        the orchestrator (agent factory, conversation).
        """
        from daemon.engine.orchestrator.agent_execution_fn import execute_agent_call

        async for evt in execute_agent_call(
            tc=tc,
            permission_callback=permission_callback,
            agent_factory=self.agent_factory,
            conversation=self.conversation,
            tool_executor=self.tool_executor,
            on_entry=self._on_entry,
        ):
            yield evt

    # -- Internal helpers --------------------------------------------------

    def _build_assistant_content(
        self,
        text_parts: list[str],
        tool_calls: list[ToolUseContent],
    ) -> list[MessageContent]:
        """Assemble assistant message content from accumulated parts."""
        content: list[MessageContent] = []
        full_text = "".join(text_parts)
        if full_text:
            content.append(TextContent(text=full_text))
        content.extend(tool_calls)
        return content

    async def _record_assistant_message(self, content: list[MessageContent]) -> None:
        """Append the assistant response to conversation history."""
        if content:
            await self.conversation.add_assistant_message(content)

    def _emit_assistant_entry(
        self,
        content: list[MessageContent],
        end_event: StreamEnd,
    ) -> None:
        """Persist the assistant turn to transcript."""
        if not self._on_entry or not content:
            return
        self._on_entry(
            AssistantMessageEntry(
                content=[c.model_dump() for c in content],
                usage={
                    "input_tokens": end_event.usage.input_tokens,
                    "output_tokens": end_event.usage.output_tokens,
                },
            )
        )

    async def _fire_stop_hooks(self) -> None:
        """Trigger all ``stop`` hooks (fire-and-forget)."""
        hook_registry = self.tool_executor._hook_registry
        stop_hooks = hook_registry.get_hooks(HookEvent.STOP)
        if stop_hooks:
            try:
                await run_hooks(stop_hooks, HookContext())
            except Exception:
                logger.exception("Error running stop hooks")

    def _maybe_trigger_extract(self) -> None:
        """Fire-and-forget memory extraction if due."""
        if self.memory_extractor is None:
            return
        self.memory_extractor.maybe_trigger(
            messages=self.conversation.get_messages(),
            message_count=self.conversation.message_count,
            memory_store=self.memory_manager.memory_store if self.memory_manager else None,
            agent_factory=self.agent_factory,
        )


# Re-export for backward compatibility.
__all__ = ["Orchestrator", "PermissionCallback"]
